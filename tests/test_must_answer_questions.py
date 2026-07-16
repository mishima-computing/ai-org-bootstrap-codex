"""Brief 27 + amendments 1-3: defer-to-code with teeth, obligation-form throughout.

Canon (deferred_to_code_canon 2026-07-05): decide irreversible/public-contract
questions on paper; resolve empirical uncertainty in code; tie every deferred
question to a later gate. Requester rulings: the author-facing artifact is
obligation-form (must_resolve_in_patch — "deferred" prompts deprioritization);
the proposal stays LEAN and the contingency is a dedicated bounded call
scheduled in parallel with Plan-A coding.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ai_org.patchwork_queue import patch_series_gate
from ai_org.patchwork_queue import receive as receive_module
from ai_org.patchwork_queue import review
from ai_org.patch_author import code_worker
from ai_org.patch_author import contract as patch_author_contract
from ai_org.patch_author import functional_check
from ai_org import contributor_handoff, review_bodies

import test_delta_scoped_review as review_harness
import test_patch_series_review as harness


def _plan(step: str = "constraints") -> dict[str, Any]:
    return {
        "step": step,
        "target_ids": ["constraint:hard:1"],
        "objections": [
            {
                "objection_id": "compat:cadence",
                "claim": "Non-TTY status cadence is unproven.",
                "requested_author_action": "provide_evidence",
                "anchor_node_ids": ["constraint:hard:1"],
                "axis": "approach",
                "resolution_authority": "author",
            },
            {
                "objection_id": "compat:cli-contract",
                "claim": "skip/reset in the first public CLI contract is undefined.",
                "requested_author_action": "revise_subtree",
                "anchor_node_ids": ["constraint:hard:1"],
                "axis": "compat",
                "resolution_authority": "author",
            },
        ],
    }


# ---------------------------------------------------------------------------
# Author proposes (lean, amendment 3) + mechanical non-deferrable guard
# ---------------------------------------------------------------------------


def test_deferral_proposal_schema_is_lean_and_enum_closed():
    schema = receive_module._targeted_revision_schema("constraints", _plan())
    proposals = schema["properties"]["deferral_proposals"]
    item_schema = proposals["items"]
    # Amendment 3: LEAN — no inline contingency field (that would re-inflate the
    # revision output schema, the output-side cognitive-debt disease itself).
    assert set(item_schema["required"]) == {"objection_id", "executable_check", "defer_reason"}
    assert "contingency_plan" not in item_schema["properties"]
    assert sorted(item_schema["properties"]["objection_id"]["enum"]) == ["compat:cadence", "compat:cli-contract"]
    assert "deferral_proposals" in schema["required"]


def test_deferral_proposal_parsing_rejects_invalid_entries():
    plan = _plan()
    valid = [{"objection_id": "compat:cadence", "executable_check": "sh probe.sh", "defer_reason": "Empirical."}]
    assert receive_module._parse_deferral_proposals(valid, plan) == [
        {"objection_id": "compat:cadence", "executable_check": "sh probe.sh", "defer_reason": "Empirical."}
    ]
    for bad in (
        [{"objection_id": "unknown:1", "executable_check": "x", "defer_reason": "y"}],
        [{"objection_id": "compat:cadence", "executable_check": " ", "defer_reason": "y"}],
        [{"objection_id": "compat:cadence", "executable_check": "x", "defer_reason": ""}],
        valid + valid,  # duplicate
        "not-a-list",
    ):
        result = receive_module._parse_deferral_proposals(bad, plan)
        assert isinstance(result, dict) and result["ok"] is False


def test_non_deferrable_guard_fires_on_public_contract_shapes_only():
    view_requester_stack = {"tech_stack": {"provenance": "requester_specified"}}
    # Live pair: the public CLI contract shape (compat x hard constraint) blocks...
    cli_contract = _plan()["objections"][1]
    assert receive_module._non_deferrable_surfaces(cli_contract, {}) == [
        "compat-axis hard-constraint commitment (public compatibility contract)"
    ]
    # ...the empirical cadence shape does not.
    cadence = _plan()["objections"][0]
    assert receive_module._non_deferrable_surfaces(cadence, {}) == []
    # Requester authority is paper by definition.
    requester = {**cadence, "resolution_authority": "requester"}
    assert "requester-authority decision (paper decides)" in receive_module._non_deferrable_surfaces(requester, {})
    # Requester-specified stack + decision-family anchor.
    stack_anchor = {**cadence, "anchor_node_ids": ["decision:candidate:one"]}
    assert "requester-specified tech_stack decision surface" in receive_module._non_deferrable_surfaces(
        stack_anchor, view_requester_stack
    )


def test_delta_ledger_records_deferral_proposed_and_blocked_proposals():
    tree = {"problem": {"id": "problem"}, "cross_links": []}
    objections = [
        {"objection_id": "compat:cadence", "anchor_node_ids": [], "status": "open"},
        {"objection_id": "compat:cli-contract", "anchor_node_ids": [], "status": "open"},
    ]
    proposals = [
        {"objection_id": "compat:cadence", "executable_check": "sh probe.sh", "defer_reason": "Empirical.", "step": "constraints"},
        {"objection_id": "compat:cli-contract", "executable_check": "x", "defer_reason": "y", "step": "constraints",
         "non_deferrable": ["compat-axis hard-constraint commitment (public compatibility contract)"]},
    ]

    delta = receive_module._revision_delta_record("series-x", 1, 2, tree, tree, objections, deferral_proposals=proposals)

    by_id = {item["objection_id"]: item for item in delta["objection_responses"]}
    assert by_id["compat:cadence"]["classification"] == "deferral_proposed"
    assert by_id["compat:cadence"]["executable_check"] == "sh probe.sh"
    # Blocked proposals never reach the reviewer as proposals; recorded, not silent.
    assert by_id["compat:cli-contract"]["classification"] == "unaddressed"
    assert delta["deferral_rejected_non_deferrable"][0]["objection_id"] == "compat:cli-contract"


def test_targeted_revision_prompt_offers_the_lean_deferral_option():
    prompt = receive_module._targeted_revision_prompt("constraints", {**_plan(), "neighborhood_ids": [],
        "manifest_ids": [], "target_bodies": {}, "neighborhood_bodies": {}, "out_of_step_anchor_bodies": {}})
    assert "deferral_proposals" in prompt
    assert "question the patch MUST answer" in prompt
    assert "contingency plan elaborated separately" in prompt
    assert "contingency_plan}" not in prompt  # lean: no inline field requested


# ---------------------------------------------------------------------------
# Blank reviewer result + committed obligation artifact
# ---------------------------------------------------------------------------


def _deferral_round_fixture(tmp_path) -> dict[str, Any]:
    harness._init_repo(tmp_path)
    objection = harness._objection("approach", objection_id="approach:cadence",
                                   anchors=["decision:anchored-review"],
                                   claim="Non-TTY status cadence is unproven.")
    review_harness._write_round_record(tmp_path, 1, objections=[objection])
    review_harness._write_delta(tmp_path, 1, {
        "changed_node_ids": ["decision:anchored-review"],
        "objection_responses": [{
            "objection_id": "approach:cadence",
            "classification": "deferral_proposed",
            "anchor_node_ids": ["decision:anchored-review"],
            "successor_node_ids": [],
            "executable_check": "sh probe_non_tty_cadence.sh",
            "defer_reason": "Cadence is empirical; only a running CLI answers it.",
        }],
    })
    return objection


def _deferral_verdict_handler(prompts, status: str):
    def handler(repo, prompt, output_schema):
        kind = harness._schema_kind(output_schema)
        prompts.append((kind, prompt))
        if kind == "aufheben":
            return harness._aufheben_payload("direction-ok"), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        if axis == "approach":
            decided = harness._objection("approach", objection_id="approach:cadence",
                                         anchors=["decision:anchored-review"],
                                         claim="Non-TTY status cadence is unproven.")
            decided["status"] = status
            return harness._axis_payload(axis, [decided]), 0
        return harness._axis_payload(axis, []), 0

    return handler


def test_reviewer_accepting_a_deferral_unblocks_and_commits_the_obligation_artifact(tmp_path, monkeypatch):
    _deferral_round_fixture(tmp_path)
    harness._patch_reference(monkeypatch)
    prompts: list[tuple[str, str]] = []
    harness._install_codex_fake(monkeypatch, _deferral_verdict_handler(prompts, "deferral_accepted"))

    result = review.run_patch_series_review(tmp_path, harness.RFC_ID)

    # An accepted deferral no longer counts as open-blocking.
    assert result.status == "direction-ok"
    record = harness._committed_round_record(tmp_path, result)
    statuses = {entry["objection_id"]: entry["status"] for entry in record["objections"]}
    assert statuses["approach:cadence"] == "deferral_accepted"
    # The compatibility transition consults the committed proposal only after
    # the blank reviewer independently returns the matching id and status.
    reviewer_prompt = next(prompt for kind, prompt in prompts if kind == "reviewer")
    assert "deferral_proposed" not in reviewer_prompt
    assert "sh probe_non_tty_cadence.sh" not in reviewer_prompt
    # The committed artifact is obligation-form: no "deferred" vocabulary on any
    # surface the patch author reads.
    artifact = contributor_handoff.parse_questions(
        harness._git(tmp_path, "show", f"{harness.RFC_BRANCH}:{patch_series_gate.PATCH_MUST_ANSWER_QUESTIONS_PATH}").stdout
    )
    [entry] = artifact["questions_the_patch_must_answer"]
    assert entry["objection_id"] == "approach:cadence"
    assert entry["status"] == "must_resolve_in_patch"
    assert entry["executable_check"] == "sh probe_non_tty_cadence.sh"
    assert entry["accepted_in_review_round"].as_int_exact() == 2
    assert entry["if_check_fails_implement"] == ""  # elaborated patch-side (amendment 3)
    assert "defer" not in json.dumps(sorted(entry.keys()))
    assert entry["status"].count("defer") == 0


def test_reviewer_rejecting_a_deferral_keeps_it_open_blocking(tmp_path, monkeypatch):
    _deferral_round_fixture(tmp_path)
    harness._patch_reference(monkeypatch)
    prompts: list[tuple[str, str]] = []
    harness._install_codex_fake(monkeypatch, _deferral_verdict_handler(prompts, "deferral_rejected"))

    result = review.run_patch_series_review(tmp_path, harness.RFC_ID)

    assert result.status == "needs_revision"
    record = harness._committed_round_record(tmp_path, result)
    statuses = {entry["objection_id"]: entry["status"] for entry in record["objections"]}
    assert statuses["approach:cadence"] == "open"  # normal blocking flow
    assert record["deferral_rejected_ids"] == ["approach:cadence"]
    tree_files = harness._git(tmp_path, "show", "--name-only", "--pretty=format:", harness.RFC_BRANCH).stdout
    assert patch_series_gate.PATCH_MUST_ANSWER_QUESTIONS_PATH not in tree_files


def test_accepted_status_without_a_pending_proposal_fails_safe_to_open(tmp_path, monkeypatch):
    # Fail-safe: the reviewer cannot mint a deferral the author never proposed.
    harness._init_repo(tmp_path)
    objection = harness._objection("approach", objection_id="approach:cadence", anchors=["decision:anchored-review"])
    review_harness._write_round_record(tmp_path, 1, objections=[objection])
    review_harness._write_delta(tmp_path, 1, {
        "changed_node_ids": ["decision:anchored-review"],
        "objection_responses": [{
            "objection_id": "approach:cadence",
            "classification": "addressed_by_change",
            "anchor_node_ids": ["decision:anchored-review"],
            "successor_node_ids": [],
        }],
    })
    harness._patch_reference(monkeypatch)
    prompts: list[tuple[str, str]] = []
    harness._install_codex_fake(monkeypatch, _deferral_verdict_handler(prompts, "deferral_accepted"))

    result = review.run_patch_series_review(tmp_path, harness.RFC_ID)

    assert result.status == "needs_revision"


# ---------------------------------------------------------------------------
# Worker plumbing: front-loaded obligations, dedicated contingency, early gate
# ---------------------------------------------------------------------------


def _question(objection_id: str = "approach:cadence", check: str = "test -f cadence-probe.txt") -> dict[str, Any]:
    return {
        "objection_id": objection_id,
        "status": "must_resolve_in_patch",
        "executable_check": check,
        "why_resolved_in_patch": "Cadence is empirical.",
        "if_check_fails_implement": "",
        "claim": "Non-TTY status cadence is unproven.",
        "anchor_node_ids": ["decision:anchored-review"],
        "axis": "approach",
        "accepted_in_review_round": 2,
    }


def test_item_windows_carry_obligations_and_no_deferred_vocabulary():
    import test_patch_author_code_worker as worker_harness

    brief = dict(worker_harness._brief())
    brief["must_answer_questions"] = [_question()]
    items = code_worker.plan_items(brief["patch_plan"], brief["plan_id"])
    window = code_worker.build_item_window(brief, items[0], [])

    assert window["must_answer_questions"][0]["objection_id"] == "approach:cadence"
    prompt = code_worker.render_item_prompt(window)
    assert "QUESTIONS THE PATCH MUST ANSWER" in prompt
    assert "test -f cadence-probe.txt" in prompt
    # Blank-window assertion (amendment 1): the author never reads "deferred".
    assert "deferred" not in prompt.lower()
    assert "deferral" not in prompt.lower()


def test_contingency_items_are_scheduled_first_and_windows_are_isolated(monkeypatch, tmp_path):
    questions = [_question("approach:cadence"), _question("compat:other", check="test -f other.txt")]
    items = code_worker.contingency_elaboration_items(questions)
    assert [item["item_id"] for item in items] == [
        "must-answer#approach:cadence#contingency",
        "must-answer#compat:other#contingency",
    ]
    # Already-elaborated questions schedule nothing.
    elaborated = [{**_question(), "if_check_fails_implement": "Use a fixed cadence."}]
    assert code_worker.contingency_elaboration_items(elaborated) == []

    # The dedicated call's window: ONLY the one question + anchors + check.
    import test_patch_author_code_worker as worker_harness

    brief = dict(worker_harness._brief())
    brief["must_answer_questions"] = questions
    calls: list[dict[str, Any]] = []

    def fake_run_json(repo, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "raw": json.dumps({
            "contingency_plan": "Fall back to a fixed 2s cadence.",
            "rewind_class": "in_patch",
            "direction_change_node_ids": [],
        })}

    monkeypatch.setattr(code_worker.codex_exec, "run_json", fake_run_json)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    subprocess.run(["git", "init", "-q", str(worktree)], check=True)

    outcome = code_worker._elaborate_contingency(
        tmp_path, worktree, brief, questions[0],
        identity={"name": "t", "email": "t@example.invalid"},
        ctx=code_worker.org_log.RunContext(repo=tmp_path, run_id="run-contingency-test"),
    )

    assert outcome["ok"] is True
    assert outcome["contingency_plan"] == "Fall back to a fixed 2s cadence."
    prompt = calls[0]["prompt"]
    assert "approach:cadence" in prompt
    assert "compat:other" not in prompt  # other questions stay out of the window
    assert "reasoning_effort" not in calls[0]  # NORMAL effort: fresh judgment
    # The plan is committed into the contribution-branch obligation artifact.
    committed = contributor_handoff.parse_questions(
        (worktree / patch_series_gate.PATCH_MUST_ANSWER_QUESTIONS_PATH).read_text(), contribution=True
    )
    by_id = {entry["objection_id"]: entry for entry in committed["questions_the_patch_must_answer"]}
    assert by_id["approach:cadence"]["if_check_fails_implement"] == "Fall back to a fixed 2s cadence."
    assert by_id["approach:cadence"]["rewind_class"] == "in_patch"


def _worker_git_worktree(tmp_path) -> Path:
    worktree = tmp_path / "wt"
    worktree.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(worktree)], check=True)
    subprocess.run(["git", "-C", str(worktree), "config", "user.email", "t@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(worktree), "config", "user.name", "t"], check=True)
    (worktree / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(worktree), "add", "seed.txt"], check=True)
    subprocess.run(["git", "-C", str(worktree), "commit", "-q", "-m", "seed"], check=True)
    return worktree


def test_must_answer_phase_passes_pivots_and_bounds_to_one_pivot(monkeypatch, tmp_path):
    import test_patch_author_code_worker as worker_harness

    brief = dict(worker_harness._brief())
    identity = {"name": "t", "email": "t@example.invalid"}
    ctx = code_worker.org_log.RunContext(repo=tmp_path, run_id="run-must-answer-test")
    worktree = _worker_git_worktree(tmp_path)

    # Passing check: outcome recorded, no pivot.
    (worktree / "cadence-probe.txt").write_text("ok\n", encoding="utf-8")
    brief["must_answer_questions"] = [_question()]
    result = code_worker._answer_must_answer_questions(tmp_path, worktree, brief, identity=identity, touched_paths=[], ctx=ctx)
    assert result == {
        "ok": True,
        "outcomes": {"approach:cadence": {"outcome": "check_passed", "executable_check": "test -f cadence-probe.txt"}},
        "contingency_engaged": [],
    }

    # Failing check with an elaborated plan: ONE pivot implements it, recheck passes.
    brief["must_answer_questions"] = [
        {**_question("approach:pivot", check="test -f pivot-made.txt"),
         "if_check_fails_implement": "Create pivot-made.txt with the fallback cadence."}
    ]
    (worktree / patch_series_gate.PATCH_MUST_ANSWER_QUESTIONS_PATH).write_text(
        json.dumps({"questions_the_patch_must_answer": brief["must_answer_questions"]}), encoding="utf-8"
    )
    real_run = subprocess.run

    def fake_codex(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd[:2] == ["codex", "exec"]:
            worktree_path = Path(cmd[cmd.index("-C") + 1])
            (worktree_path / "pivot-made.txt").write_text("fallback\n", encoding="utf-8")
            Path(cmd[cmd.index("-o") + 1]).write_text("done\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(code_worker.subprocess, "run", fake_codex)
    result = code_worker._answer_must_answer_questions(tmp_path, worktree, brief, identity=identity, touched_paths=[], ctx=ctx)
    assert result["ok"] is True
    assert result["contingency_engaged"] == ["approach:pivot"]
    assert result["outcomes"]["approach:pivot"]["outcome"] == "check_passed_via_contingency"

    # A failing contingency is typed fail-closed — no plan C/D ladder.
    brief["must_answer_questions"] = [
        {**_question("approach:doomed", check="test -f never-made.txt"),
         "if_check_fails_implement": "This plan does not create the file."}
    ]
    (worktree / patch_series_gate.PATCH_MUST_ANSWER_QUESTIONS_PATH).write_text(
        json.dumps({"questions_the_patch_must_answer": brief["must_answer_questions"]}), encoding="utf-8"
    )

    def fake_codex_noop(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd[:2] == ["codex", "exec"]:
            worktree_path = Path(cmd[cmd.index("-C") + 1])
            (worktree_path / "attempt-log.txt").write_text("tried\n", encoding="utf-8")
            Path(cmd[cmd.index("-o") + 1]).write_text("done\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(code_worker.subprocess, "run", fake_codex_noop)
    result = code_worker._answer_must_answer_questions(tmp_path, worktree, brief, identity=identity, touched_paths=[], ctx=ctx)
    assert result["ok"] is False
    assert result["status"] == "must_answer_question_failed"
    assert result["objection_id"] == "approach:doomed"
    assert result["contingency_engaged"] == ["approach:doomed"]


def test_result_artifact_requires_an_outcome_per_question(tmp_path):
    import test_patch_author_code_worker as worker_harness

    brief = dict(worker_harness._brief())
    brief["must_answer_questions"] = [_question()]
    worktree = _worker_git_worktree(tmp_path)
    identity = {"name": "t", "email": "t@example.invalid"}

    missing = code_worker._write_result_artifact(tmp_path, worktree, brief, identity=identity, must_answer_outcomes={})
    assert missing == {"ok": False, "status": "must_answer_outcome_missing", "objection_ids": ["approach:cadence"]}

    outcomes = {"approach:cadence": {"outcome": "check_passed", "executable_check": "test -f cadence-probe.txt"}}
    written = code_worker._write_result_artifact(tmp_path, worktree, brief, identity=identity, must_answer_outcomes=outcomes)
    assert written["ok"] is True
    result = contributor_handoff.parse_result((worktree / code_worker.RESULT_ARTIFACT_PATH).read_text())
    assert result["must_answer_outcomes"] == outcomes
    # The committed contract admits the field and documents the obligation.
    schema = patch_author_contract.implementation_result_schema()
    assert "must_answer_outcomes" in schema["properties"]
    assert "questions-the-patch-must-answer" in schema["x-questions-the-patch-must-answer"]
    assert code_worker.validate_result_against_schema(result, schema) == []


def test_result_artifact_rejects_outcomes_outside_the_exact_question_set(tmp_path):
    import test_patch_author_code_worker as worker_harness

    brief = dict(worker_harness._brief())
    brief["must_answer_questions"] = [_question()]
    worktree = _worker_git_worktree(tmp_path)
    result = code_worker._write_result_artifact(
        tmp_path,
        worktree,
        brief,
        identity={"name": "t", "email": "t@example.invalid"},
        must_answer_outcomes={
            "approach:cadence": {"outcome": "check_passed"},
            "approach:stale": {"outcome": "check_passed"},
        },
    )

    assert result == {
        "ok": False,
        "status": "must_answer_outcome_unexpected",
        "objection_ids": ["approach:stale"],
    }


# ---------------------------------------------------------------------------
# Acceptance backstop (functional_check teeth)
# ---------------------------------------------------------------------------


def _teeth_fixture(tmp_path, *, questions: list[dict[str, Any]], result: dict[str, Any] | None):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / patch_series_gate.PATCH_MUST_ANSWER_QUESTIONS_PATH).write_text(
        json.dumps({"questions_the_patch_must_answer": questions}), encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "series"], check=True)
    subprocess.run(["git", "-C", str(repo), "branch", "-M", "series-branch"], check=True)
    worktree = tmp_path / "contrib"
    worktree.mkdir()
    if result is not None:
        (worktree / "implementation-result.json").write_text(json.dumps(result), encoding="utf-8")
    return repo, worktree


def test_acceptance_backstop_fails_closed_without_question_outcomes(tmp_path):
    questions = [_question()]
    base_result = {"patch_series_branch": "series-branch", "node_key": "root", "acknowledged_patchwork_checks": {}}

    repo, worktree = _teeth_fixture(tmp_path, questions=questions, result=base_result)
    rejection = functional_check._must_answer_questions_rejection(repo, worktree, patch_series_branch="series-branch")
    assert rejection is not None
    assert "must_answer_question_unanswered" in rejection["why"]
    assert "approach:cadence" in rejection["why"]

    answered = {**base_result, "must_answer_outcomes": {"approach:cadence": {"outcome": "check_passed"}}}
    repo2, worktree2 = _teeth_fixture(tmp_path / "two", questions=questions, result=answered)
    assert functional_check._must_answer_questions_rejection(repo2, worktree2, patch_series_branch="series-branch") is None

    extra = {
        **base_result,
        "must_answer_outcomes": {
            "approach:cadence": {"outcome": "check_passed"},
            "approach:stale": {"outcome": "check_passed"},
        },
    }
    repo_extra, worktree_extra = _teeth_fixture(
        tmp_path / "extra", questions=questions, result=extra
    )
    rejection = functional_check._must_answer_questions_rejection(
        repo_extra, worktree_extra, patch_series_branch="series-branch"
    )
    assert rejection is not None
    assert rejection["why"] == "must_answer_outcome_unexpected: approach:stale"

    # No artifact on the series branch -> no obligation, no blocker.
    repo3, worktree3 = _teeth_fixture(tmp_path / "three", questions=[], result=base_result)
    assert functional_check._must_answer_questions_rejection(repo3, worktree3, patch_series_branch="series-branch") is None


# ---------------------------------------------------------------------------
# Amendments 4 + 5: rewind depth by node position; experience returns as evidence
# ---------------------------------------------------------------------------

import test_patch_series_receive as receive_harness


def test_contingency_output_with_unknown_node_ids_is_rejected_with_feedback(monkeypatch, tmp_path):
    import test_patch_author_code_worker as worker_harness

    brief = dict(worker_harness._brief())
    brief["must_answer_questions"] = [_question()]
    calls: list[dict[str, Any]] = []

    def fake_run_json(repo, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "raw": json.dumps({
            "contingency_plan": "Plan B.",
            "rewind_class": "requires_direction_change",
            "direction_change_node_ids": ["decision:ghost"],
        })}

    monkeypatch.setattr(code_worker.codex_exec, "run_json", fake_run_json)
    worktree = _worker_git_worktree(tmp_path)

    outcome = code_worker._elaborate_contingency(
        tmp_path, worktree, brief, _question(),
        identity={"name": "t", "email": "t@example.invalid"},
        ctx=code_worker.org_log.RunContext(repo=tmp_path, run_id="run-unknown-ids"),
    )

    assert outcome["ok"] is False
    assert outcome["status"] == "contingency_elaboration_failed"
    assert "decision:ghost" in outcome["detail"]
    # One bounded feedback retry, and the feedback names the unknown ids.
    assert len(calls) == 2
    assert "decision:ghost" in calls[1]["prompt"]


def test_direction_change_contingency_escalates_with_report_and_typed_stop(monkeypatch, tmp_path):
    import test_patch_author_code_worker as worker_harness

    # A series repo the report can land on.
    repo = receive_harness._init_repo(tmp_path)
    series_branch = "ai-org/patch-series/reviewable-patch_series"
    receive_harness._git(repo, "checkout", "-B", series_branch, "main")
    (repo / "marker.txt").write_text("series\n", encoding="utf-8")
    receive_harness._git(repo, "add", "marker.txt")
    receive_harness._git(repo, "commit", "-m", "series base")
    receive_harness._git(repo, "checkout", "main")

    brief = dict(worker_harness._brief())
    brief["series_branch"] = series_branch
    question = {**_question("approach:direction", check="test -f never-there.txt")}
    brief["must_answer_questions"] = [question]
    worktree = _worker_git_worktree(tmp_path / "wtparent")
    # Pre-elaborated contingency declaring direction contamination.
    (worktree / patch_series_gate.PATCH_MUST_ANSWER_QUESTIONS_PATH).write_text(
        json.dumps({"questions_the_patch_must_answer": [{
            **question,
            "if_check_fails_implement": "Plan B: replace the phase driver contract.",
            "rewind_class": "requires_direction_change",
            "direction_change_node_ids": ["decision:anchored-review"],
        }]}), encoding="utf-8",
    )
    pivot_calls: list[object] = []
    real_run = subprocess.run

    def no_codex(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd[:2] == ["codex", "exec"]:
            pivot_calls.append(cmd)
            raise AssertionError("the worker must not improvise design on direction contamination")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(code_worker.subprocess, "run", no_codex)

    result = code_worker._answer_must_answer_questions(
        repo, worktree, brief,
        identity={"name": "t", "email": "t@example.invalid"},
        touched_paths=[],
        ctx=code_worker.org_log.RunContext(repo=repo, run_id="run-direction-change"),
    )

    assert result["ok"] is False
    assert result["status"] == "direction_change_required"
    assert result["objection_id"] == "approach:direction"
    assert result["invalidated_node_ids"] == ["decision:anchored-review"]
    assert pivot_calls == []  # jurisdiction: no improvised design work
    # The report landed on the SERIES branch, with Plan B riding verbatim, and
    # the commit touched ONLY the evidence artifact (the committed tree is NOT
    # rewritten at escalation time).
    report = contributor_handoff.parse_experience(
        receive_harness._git(repo, "show", f"{series_branch}:{patch_series_gate.IMPLEMENTATION_EXPERIENCE_REPORT_PATH}") + "\n"
    )
    [entry] = report["implementation_experience_reports"]
    assert entry["objection_id"] == "approach:direction"
    assert entry["invalidated_node_ids"] == ["decision:anchored-review"]
    assert entry["contingency_plan"] == "Plan B: replace the phase driver contract."
    changed = receive_harness._git(repo, "show", "--name-only", "--pretty=format:", series_branch).splitlines()
    assert changed == [patch_series_gate.IMPLEMENTATION_EXPERIENCE_REPORT_PATH]


def test_experience_report_reenters_reform_as_targeted_evidence(monkeypatch, tmp_path):
    receive_harness._install_historical_reform_preparation(monkeypatch)
    # The full amendment-5 circle, reform side: the report becomes an
    # objection-grade input anchored on the named nodes; brief25 targeted
    # revision runs seeded with Plan B verbatim; the ledger classifies
    # revised_from_implementation_experience; the report is marked processed.
    repo = receive_harness._init_repo(tmp_path)
    series_branch = "ai-org/patch-series/reviewable-patch_series"
    tree = receive_harness._reform_approach_tree("old slice")
    tree["problem"]["constraints"]["hard"] = [
        {"id": "constraint:hard:1", "statement": "Same branch v2.",
         "derivation": {"from": "repo", "trace": "Repo evidence."},
         "implication": {"must": "Honor.", "must_not": "Ignore."}},
        {"id": "constraint:hard:2", "statement": "Keep the CLI dependency-free.",
         "derivation": {"from": "repo", "trace": "Repo evidence."},
         "implication": {"must": "Honor.", "must_not": "Ignore."}},
        {"id": "constraint:hard:3", "statement": "Verify headlessly.",
         "derivation": {"from": "repo", "trace": "Repo evidence."},
         "implication": {"must": "Honor.", "must_not": "Ignore."}},
    ]
    view = receive_harness._patch_series_view("Reviewable patch series")
    receive_harness._git(repo, "checkout", "-B", series_branch, "main")
    (repo / "patch-series-cover-letter.json").write_text(json.dumps(view) + "\n", encoding="utf-8")
    (repo / "technical-approach-plan.json").write_text(json.dumps(tree) + "\n", encoding="utf-8")
    (repo / patch_series_gate.IMPLEMENTATION_EXPERIENCE_REPORT_PATH).write_text(
        json.dumps({"implementation_experience_reports": [{
            "objection_id": "approach:cadence",
            "claim": "Non-TTY status cadence is unproven.",
            "executable_check": "sh probe_non_tty_cadence.sh",
            "check_failure_evidence": "exit=1 cadence drift beyond budget",
            "invalidated_node_ids": ["constraint:hard:1"],
            "contingency_plan": "Plan B: fix cadence at 2s and drop adaptive pacing.",
            "rewind_class": "requires_direction_change",
            "reported_from_node_key": "root",
        }]}) + "\n", encoding="utf-8",
    )
    receive_harness._git(repo, "add", "-A")
    receive_harness._git(repo, "commit", "-m", "initial patch_series")
    receive_harness._git(repo, "commit", "--allow-empty", "-m", "patch_series: direction-ok serial 1")
    receive_harness._git(repo, "checkout", "main")
    # A round record exists (direction-ok era) with NO open objections: the
    # experience report is the only reform input.
    receive_harness._write_review_round(repo, "reviewable-patch_series", [])
    codex_calls: list[dict[str, Any]] = []

    def fake_run_json(repo_path, **kwargs):
        codex_calls.append(kwargs)
        return {"ok": True, "raw": json.dumps({
            "hard_constraints": [{
                "id": "constraint:hard:1",
                "statement": "Fix cadence at 2s; adaptive pacing is dropped.",
                "derivation": {"from": "repo", "trace": "Implementation experience evidence."},
                "implication": {"must": "Emit status every 2s.", "must_not": "Adapt cadence dynamically."},
            }],
            "soft_preferences": [],
            "deferral_proposals": [],
        })}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    assert receive_module.implementation_experience_pending(repo, "reviewable-patch_series") is True
    result = receive_module.reform_patch_series(repo, "reviewable-patch_series")

    assert result["status"] == "reformed"
    # Brief25 targeted revision, seeded: ONE bounded call whose window carries
    # the report evidence and Plan B verbatim (input evidence, never
    # auto-applied content), targeting exactly the named node.
    assert len(codex_calls) == 1
    prompt = codex_calls[0]["prompt"]
    assert "Plan B: fix cadence at 2s and drop adaptive pacing." in prompt
    assert "cadence drift beyond budget" in prompt
    assert "constraint:hard:1" in prompt
    schema = codex_calls[0]["schema"]
    assert schema["properties"]["hard_constraints"]["items"]["properties"]["id"]["enum"] == ["constraint:hard:1"]
    # The revision landed in the anchored node; ledger classifies the response
    # as evidence-consumed (obligation/evidence framing, no "deferred").
    revised_tree = json.loads(
        receive_harness._git(repo, "show", f"{series_branch}:technical-approach-plan.json")
    )
    assert revised_tree["problem"]["constraints"]["hard"][0]["statement"].startswith("Fix cadence at 2s")
    ledger = review_bodies.read_record(repo, series_branch, result["revision_delta_path"], review_bodies.REVISION_DELTA_RECIPE)
    responses = {item["objection_id"]: item for item in ledger["objection_responses"]}
    experience_id = "implementation-experience:approach:cadence"
    assert responses[experience_id]["classification"] == "revised_from_implementation_experience"
    # The consumed report is marked processed in the same commit.
    report = contributor_handoff.parse_experience(
        receive_harness._git(repo, "show", f"{series_branch}:{patch_series_gate.IMPLEMENTATION_EXPERIENCE_REPORT_PATH}") + "\n"
    )
    assert report["implementation_experience_reports"][0]["processed_in_author_version"].as_int_exact() == 2
    assert receive_module.implementation_experience_pending(repo, "reviewable-patch_series") is False
    # Amendment 5 item 3: the NEXT round's delta scope is exactly Plan B's
    # consequences — the revised node (+ neighborhood), unrelated bodies absent.
    scope = review._delta_review_scope(revised_tree, ledger)
    assert "constraint:hard:1" in scope["changed_nodes"]
    assert "Fix cadence at 2s" in json.dumps(scope["changed_nodes"])
    # The ancestor bubble ("problem" changed because its child did) does not
    # smuggle the tree back in: unrelated nodes stay id-manifest-only.
    assert "problem" not in scope["changed_nodes"]
    assert "constraint:hard:3" in scope["unchanged_node_ids"]
    assert "candidate:one" in scope["unchanged_node_ids"]
