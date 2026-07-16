"""Brief 33: asymmetric review — the reviewer is a filter, never a ceiling.

Ratified 2026-07-05 (requester: レビュワーが制約になるのは絶対にあってはならない). The reviewer-charter
freeze (briefs 19/20) was LIFTED for exactly this scope: reviewer
self-declaration (confidence + reviewed scope), author defense as a
first-class response, insistence-is-not-evidence in verdict derivation, the
anti-sycophancy counterfactual in the author's revision window, and bounded
citation-verification search. Zero-softening still binds: sharpness sentences
are byte-identical, and every added text passed a blank-context Codex probe
(scratchpad/brief33_probes/out_{a,b,c}.txt) before shipping.

Live disease: run 2's author abandoned a defensible framework selection under
a provide_evidence objection instead of defending (canon Part B: wrong-critique
compliance is a measured LLM trait, up to 15pp correctness loss).
"""
from __future__ import annotations

import json
from typing import Any

from ai_org.patchwork_queue import receive as receive_module
from ai_org.patchwork_queue import review
from ai_org import review_bodies

import test_patch_series_receive as receive_harness
import test_patch_series_review as review_harness
import test_targeted_reform_revision as targeted

RFC_ID = review_harness.RFC_ID
RFC_BRANCH = review_harness.RFC_BRANCH


# ---------------------------------------------------------------------------
# 1. Reviewer self-declaration: schema/parser parity
# ---------------------------------------------------------------------------


def test_objection_schema_declares_confidence_and_scope_and_required_covers_all():
    schema = review.build_objection_item_schema()
    assert schema["properties"]["reviewer_confidence"]["enum"] == ["high", "medium", "low"]
    assert schema["properties"]["reviewed_scope"]["type"] == "string"
    # Codex safe subset: required lists every property (and parity with the
    # exact-keyset lint follows from both reading the same builder).
    assert set(schema["required"]) == set(schema["properties"])


def test_parse_objection_round_trips_declaration_and_defaults_for_old_records():
    item = review_harness._objection("approach", reviewer_confidence="low", reviewed_scope="Only the decision node.")
    parsed = review._parse_objection(item)
    assert parsed.reviewer_confidence == "low"
    assert parsed.reviewed_scope == "Only the decision node."
    assert review._objection_record(parsed)["reviewer_confidence"] == "low"
    assert review._objection_record(parsed)["reviewed_scope"] == "Only the decision node."

    # Committed pre-brief-33 round records lack the fields: parse must not raise.
    legacy = {key: value for key, value in item.items() if key not in {"reviewer_confidence", "reviewed_scope"}}
    parsed_legacy = review._parse_objection(legacy)
    assert parsed_legacy.reviewer_confidence == ""
    assert parsed_legacy.reviewed_scope == ""


def test_missing_reviewed_scope_is_a_lint_error_but_values_never_gate():
    item = review_harness._objection("approach", reviewed_scope="")
    errors = review._lint_objection(item, {"decision:anchored-review"}, "approach", "objections[0]", set())
    assert any("reviewed_scope" in error for error in errors)
    # low confidence is NOT an error, NOT a downgrade: informational only.
    low = review_harness._objection("approach", reviewer_confidence="low")
    assert review._lint_objection(low, {"decision:anchored-review"}, "approach", "objections[0]", set()) == []


def test_charter_carries_declaration_and_verification_lines_with_sharpness_intact():
    prompt = review._review_prompt(
        review.DIMENSIONS[0],
        review_harness._patch_series_view(),
        review_harness._approach_tree(),
        {"decision:anchored-review"},
        [],
        "",
    )
    # The ratified charter additions (wording passed blank-context probe A).
    assert "declare reviewer_confidence (high, medium, or low) and reviewed_scope" in prompt
    assert "verification of presented citations only, not open research" in prompt
    assert "the burden of proof stays with the author" in prompt
    # Zero-softening: the sharpness sentences are byte-identical.
    assert "Blocking objections need concrete impact." in prompt
    assert "Preferences without evidence and impact must be nonblocking_suggestion or style_defer." in prompt


# ---------------------------------------------------------------------------
# 2. Author defense as a first-class response (receive side)
# ---------------------------------------------------------------------------


def test_defense_prompt_carries_counterfactual_instruction_byte_for_byte():
    tree = targeted._plan_tree()
    plan = receive_module._targeted_revision_plan(
        tree, [targeted._objection("compat:1", ["constraint:hard:1"])], ["constraints"]
    )["constraints"]
    prompt = receive_module._targeted_revision_prompt("constraints", plan)
    # Wording that passed blank-context probe B (revise on a correct objection,
    # defend on a mistaken one) ships byte-for-byte.
    assert (
        "Revise only if the concern survives the counterfactual check: would this change still be right if "
        "the objection asserted the opposite?" in prompt
    )
    assert "A revision caused solely by reviewer insistence is wrong" in prompt
    assert "Leave defenses empty when every objection warrants revision." in prompt


def test_targeted_schema_requires_defenses_envelope_with_enum_closed_ids():
    tree = targeted._plan_tree()
    plan = receive_module._targeted_revision_plan(
        tree, [targeted._objection("compat:1", ["constraint:hard:1"])], ["constraints"]
    )["constraints"]
    schema = receive_module._targeted_revision_schema("constraints", plan)
    assert "defenses" in schema["required"]
    items = schema["properties"]["defenses"]["items"]
    assert set(items["required"]) == {"objection_id", "defense", "evidence_citations"}


def test_defense_validation_rejects_unknown_uncited_and_contradictory_entries():
    tree = targeted._plan_tree()
    plan = receive_module._targeted_revision_plan(
        tree, [targeted._objection("compat:1", ["constraint:hard:1"])], ["constraints"]
    )["constraints"]
    unknown = receive_module._parse_defenses(
        [{"objection_id": "ghost:1", "defense": "x", "evidence_citations": ["c"]}], plan
    )
    uncited = receive_module._parse_defenses(
        [{"objection_id": "compat:1", "defense": "x", "evidence_citations": [" "]}], plan
    )
    assert unknown["ok"] is False and "defense for 'ghost:1'" in unknown["error"]
    assert uncited["ok"] is False and "at least one evidence citation" in uncited["error"]

    # Defend + defer the same objection is contradictory: typed reject.
    raw = json.dumps(
        {
            "hard_constraints": [],
            "soft_preferences": [],
            "deferral_proposals": [
                {"objection_id": "compat:1", "executable_check": "true", "defer_reason": "empirical"}
            ],
            "closed_questions": [],
            "defenses": [
                {"objection_id": "compat:1", "defense": "It is inapplicable.", "evidence_citations": ["constraint:hard:1"]}
            ],
        }
    )
    result = receive_module._parse_targeted_fragments("constraints", raw, plan)
    assert result["ok"] is False
    assert "both defended and deferred" in result["error"]


def test_reform_defense_lands_in_ledger_as_defended_with_evidence(tmp_path, monkeypatch):
    receive_harness._install_historical_reform_preparation(monkeypatch)
    repo = receive_harness._init_repo(tmp_path)
    patch_series_view = receive_harness._patch_series_view("Reviewable patch series")
    tree = targeted._plan_tree()
    branch = "ai-org/patch-series/reviewable-patch_series"
    receive_harness._git(repo, "checkout", "-B", branch, "main")
    (repo / "patch-series-cover-letter.json").write_text(json.dumps(patch_series_view) + "\n", encoding="utf-8")
    (repo / "technical-approach-plan.json").write_text(json.dumps(tree) + "\n", encoding="utf-8")
    receive_harness._git(repo, "add", "patch-series-cover-letter.json", "technical-approach-plan.json")
    receive_harness._git(repo, "commit", "-m", "initial patch_series")
    receive_harness._git(repo, "commit", "--allow-empty", "-m", "patch_series: needs-revision round 1")
    receive_harness._git(repo, "checkout", "main")
    objection = receive_harness._blocking_objection("prior_art:pattern-b")
    receive_harness._write_review_round(repo, "reviewable-patch_series", objection)

    def fake_run_json(repo_path, **kwargs):
        return {
            "ok": True,
            "raw": json.dumps(
                {
                    "patterns": [],
                    "defenses": [
                        {
                            "objection_id": str(objection["objection_id"]),
                            "defense": "The pattern's when_applies already covers the objected case; the objection misreads it.",
                            "evidence_citations": ["prior_art:pattern-b", "kernel docs"],
                        }
                    ],
                }
            ),
        }

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module.reform_patch_series(repo, "reviewable-patch_series")

    assert result["status"] == "reformed"
    ledger = review_bodies.read_record(
        repo, branch, result["revision_delta_path"], review_bodies.REVISION_DELTA_RECIPE
    )
    responses = {item["objection_id"]: item for item in ledger["objection_responses"]}
    answer = responses[str(objection["objection_id"])]
    assert answer["classification"] == "defended_with_evidence"
    assert answer["defense"].startswith("The pattern's when_applies already covers")
    assert answer["evidence_citations"] == ["prior_art:pattern-b", "kernel docs"]
    # No node change: the tree is untouched by a pure defense.
    revised_tree = json.loads(receive_harness._git(repo, "show", f"{branch}:technical-approach-plan.json"))
    assert revised_tree["problem"]["prior_art"] == tree["problem"]["prior_art"]


# ---------------------------------------------------------------------------
# 3. Defense stays with author self-history; reviewers read the current paper
# ---------------------------------------------------------------------------


def _seed_defended_round(tmp_path, objection: dict[str, Any], defense_text: str) -> None:
    review_harness._write_round_record(tmp_path, 1, objection)
    directory = tmp_path / ".ai-org" / "review" / RFC_ID
    directory.mkdir(parents=True, exist_ok=True)
    ledger = {
        "patch_series_id": RFC_ID,
        "review_round": 1,
        "author_version": 2,
        "added_node_ids": [],
        "pruned_node_ids": [],
        "changed_node_ids": [],
        "replacements": [],
        "objection_responses": [
            {
                "objection_id": str(objection["objection_id"]),
                "classification": "defended_with_evidence",
                "anchor_node_ids": list(objection["anchor_node_ids"]),
                "successor_node_ids": [],
                "defense": defense_text,
                "evidence_citations": ["decision:anchored-review"],
            }
        ],
    }
    (directory / "round-0001-revision-delta.json").write_text(json.dumps(ledger) + "\n", encoding="utf-8")


def test_next_round_reviewer_input_does_not_carry_the_defense(tmp_path, monkeypatch):
    review_harness._init_repo(tmp_path)
    review_harness._patch_reference(monkeypatch)
    blocker = review_harness._objection("approach", objection_id="approach:framework")
    defense_text = "The framework selection is grounded in the repo's existing dependency set; the objection asserts a policy the requester never set."
    _seed_defended_round(tmp_path, blocker, defense_text)
    prompts: list[tuple[str, str]] = []

    def handler(repo, prompt, output_schema):
        kind = review_harness._schema_kind(output_schema)
        prompts.append((kind, prompt))
        if kind == "aufheben":
            return review_harness._aufheben_payload("direction-ok", []), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        return review_harness._axis_payload(axis, []), 0

    review_harness._install_codex_fake(monkeypatch, handler)

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    assert result.status == "direction-ok"
    reviewer_prompts = [prompt for kind, prompt in prompts if kind == "reviewer"]
    # Author self-history owns replies; the reviewer gets a blank current paper.
    assert reviewer_prompts
    assert all(defense_text not in prompt for prompt in reviewer_prompts)
    assert all("defended_with_evidence" not in prompt for prompt in reviewer_prompts)
    record = review_harness._committed_round_record(tmp_path, result)
    assert record["cross_round_objection_mapping"][0]["status"] == "resolved_by_construction"


def test_fresh_counterpart_stays_open_even_when_prior_author_defended(tmp_path, monkeypatch):
    review_harness._init_repo(tmp_path)
    review_harness._patch_reference(monkeypatch)
    blocker = review_harness._objection("approach", objection_id="approach:framework")
    _seed_defended_round(tmp_path, blocker, "Defended with cited evidence.")

    def handler(repo, prompt, output_schema):
        kind = review_harness._schema_kind(output_schema)
        if kind == "aufheben":
            return review_harness._aufheben_payload("needs_revision", [blocker]), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        # The reviewer re-raises the defended objection with the IDENTICAL
        # evidence set: insistence, not evidence.
        return review_harness._axis_payload(axis, [blocker] if axis == "approach" else []), 0

    review_harness._install_codex_fake(monkeypatch, handler)

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    # A blank reading independently raised the concern again, so resolution is a
    # property of the text and the objection remains open.
    assert result.status == "needs_revision"
    assert [objection.objection_id for objection in result.unresolved] == ["approach:framework"]
    record = review_harness._committed_round_record(tmp_path, result)
    entry = next(item for item in record["objections"] if item["objection_id"] == "approach:framework")
    assert entry["status"] == "open"
    assert record["consolidation_raw_verdict"] == "needs_revision"
    assert record["cross_round_objection_mapping"] == [
        {
            "prior_objection_id": "approach:framework",
            "axis": "approach",
            "prior_anchor_node_ids": ["decision:anchored-review"],
            "status": "unresolved",
            "fresh_objection_ids": ["approach:framework"],
        }
    ]


def test_reraise_with_new_evidence_blocks_normally(tmp_path, monkeypatch):
    review_harness._init_repo(tmp_path)
    review_harness._patch_reference(monkeypatch)
    blocker = review_harness._objection("approach", objection_id="approach:framework")
    _seed_defended_round(tmp_path, blocker, "Defended with cited evidence.")
    new_evidence = list(blocker["evidence"]) + [
        {
            "source_type": "repo_fact",
            "citation": "requirements.txt pins the competing framework at line 3.",
            "consulted_terms": [],
        }
    ]
    reraised = review_harness._objection(
        "approach", objection_id="approach:framework", evidence=new_evidence
    )

    def handler(repo, prompt, output_schema):
        kind = review_harness._schema_kind(output_schema)
        if kind == "aufheben":
            return review_harness._aufheben_payload("needs_revision", [reraised]), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        return review_harness._axis_payload(axis, [reraised] if axis == "approach" else []), 0

    review_harness._install_codex_fake(monkeypatch, handler)

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    # New evidence answers the defense: the normal blocking flow is untouched.
    assert result.status == "needs_revision"
    assert [objection.objection_id for objection in result.unresolved] == ["approach:framework"]
    record = review_harness._committed_round_record(tmp_path, result)
    entry = next(item for item in record["objections"] if item["objection_id"] == "approach:framework")
    assert entry["status"] == "open"


def test_consolidation_may_still_uphold_an_insisted_objection_explicitly(tmp_path, monkeypatch):
    # Canon rule 5: the designated arbiter (consolidation, no new judge) can
    # uphold with explicit reasons — an evidenced nak passes through untouched.
    review_harness._init_repo(tmp_path)
    review_harness._patch_reference(monkeypatch)
    blocker = review_harness._objection("approach", objection_id="approach:framework")
    _seed_defended_round(tmp_path, blocker, "Defended with cited evidence.")
    reason = "Upheld: the defense does not answer the dependency conflict; the cited nodes show the opposite."

    def handler(repo, prompt, output_schema):
        kind = review_harness._schema_kind(output_schema)
        if kind == "aufheben":
            return review_harness._aufheben_payload("nak", [blocker], nak_reason=reason), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        return review_harness._axis_payload(axis, [blocker] if axis == "approach" else []), 0

    review_harness._install_codex_fake(monkeypatch, handler)

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    assert result.status == "nak"
    assert result.escalation_reason == reason
