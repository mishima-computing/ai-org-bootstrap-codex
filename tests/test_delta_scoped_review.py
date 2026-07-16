"""Review-side tests for fresh reviewer papers and continuity bookkeeping."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_org.patchwork_queue import review

import test_patch_series_review as harness

RFC_ID = harness.RFC_ID
RFC_BRANCH = harness.RFC_BRANCH


def _write_round_record(repo: Path, round_number: int, *, objections: list[dict[str, Any]],
                        resolved_objections: list[dict[str, Any]] | None = None) -> None:
    directory = repo / ".ai-org" / "review" / RFC_ID
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "patch_series_id": RFC_ID,
        "branch": RFC_BRANCH,
        "round": round_number,
        "inputs": {"patch_series_path": "patch-series-cover-letter.json",
                   "technical_approach_path": "technical-approach-plan.json", "node_ids": []},
        "axis_reviews": [],
        "objections": objections,
        "resolved_objections": resolved_objections or [],
        "per_axis_verdicts": {},
        "consolidation": {},
        "verdict": "needs_revision",
        "git_result_marker": f"patch_series: needs-revision round {round_number}",
    }
    (directory / f"round-{round_number}.json").write_text(json.dumps(record) + "\n", encoding="utf-8")


def _write_delta(repo: Path, round_number: int, delta: dict[str, Any]) -> None:
    directory = repo / ".ai-org" / "review" / RFC_ID
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "patch_series_id": RFC_ID,
        "review_round": round_number,
        "author_version": round_number + 1,
        "added_node_ids": [],
        "changed_node_ids": [],
        "pruned_node_ids": [],
        "node_replacements": [],
        "objection_responses": [],
        **delta,
    }
    (directory / f"round-{round_number:04d}-revision-delta.json").write_text(
        json.dumps(payload) + "\n", encoding="utf-8"
    )


def _quiet_handler(prompts: list[tuple[str, str]], aufheben_payload: str | None = None):
    def handler(repo, prompt, output_schema):
        kind = harness._schema_kind(output_schema)
        prompts.append((kind, prompt))
        if kind == "aufheben":
            return aufheben_payload or harness._aufheben_payload("direction-ok"), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        return harness._axis_payload(axis, []), 0

    return handler


# ---------------------------------------------------------------------------
# Component D: delta-scoped round-N input + neighborhood amendment
# ---------------------------------------------------------------------------


def test_delta_scope_builder_includes_one_hop_neighborhood_and_excludes_ancestors():
    approach = harness._approach_tree()
    delta = {"changed_node_ids": ["decision:anchored-review"], "added_node_ids": [], "node_replacements": []}

    scope = review._delta_review_scope(approach, delta)

    assert "decision:anchored-review" in scope["changed_nodes"]
    # Ratified amendment: the changed decision references the unchanged selected
    # candidate (selected_candidate_id edge) -> that candidate's BODY is in scope.
    assert "candidate:anchored-review" in scope["changed_nodes"]
    # Structural ancestors of a seed never re-enter as neighbors (they would smuggle
    # the whole tree back in), and unrelated nodes stay id-manifest-only.
    assert "question:approach" not in scope["scope_node_ids"]
    assert "candidate:status-quo" in scope["unchanged_node_ids"]
    assert "prior_art:lkml" in scope["unchanged_node_ids"]
    assert scope == review._delta_review_scope(approach, delta)


def test_round_two_reviewer_paper_is_fresh_and_contains_no_prior_dialogue(tmp_path, monkeypatch):
    harness._init_repo(tmp_path)
    harness._patch_reference(monkeypatch)
    objection = harness._objection("approach", anchors=["decision:anchored-review"])
    _write_round_record(tmp_path, 1, objections=[objection])
    _write_delta(tmp_path, 1, {
        "changed_node_ids": ["decision:anchored-review"],
        "objection_responses": [{
            "objection_id": objection["objection_id"],
            "classification": "addressed_by_change",
            "anchor_node_ids": ["decision:anchored-review"],
            "successor_node_ids": [],
        }],
    })
    prompts: list[tuple[str, str]] = []
    harness._install_codex_fake(monkeypatch, _quiet_handler(prompts))

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    assert result.status == "direction-ok"
    reviewer_prompts = [prompt for kind, prompt in prompts if kind == "reviewer"]
    assert reviewer_prompts
    assert len(reviewer_prompts) == len(review._round_one_shards(harness._approach_tree())) * len(review.DIMENSIONS)
    for prompt in reviewer_prompts:
        assert "The current paper is sharded by step subtree" in prompt
        assert "scoped to the revision delta" not in prompt
        assert objection["claim"] not in prompt
        assert "addressed_by_change" not in prompt
    # The current-body shards collectively carry the full approach.
    assert any('"selected_candidate_id": "candidate:anchored-review"' in prompt for prompt in reviewer_prompts)
    assert any("LKML patch series threads use anchored replies." in prompt for prompt in reviewer_prompts)
    # Aufheben sees current paper plus only this blank round's objections.
    aufheben_prompt = next(prompt for kind, prompt in prompts if kind == "aufheben")
    assert "scoped to the revision delta" not in aufheben_prompt
    assert objection["claim"] not in aufheben_prompt
    assert "addressed_by_change" not in aufheben_prompt
    # The blank reviewer did not raise a counterpart, so the prior objection is
    # resolved by construction in the additive cross-round mapping.
    record = harness._committed_round_record(tmp_path, result)
    assert record["cross_round_objection_mapping"] == [
        {
            "prior_objection_id": objection["objection_id"],
            "axis": "approach",
            "prior_anchor_node_ids": ["decision:anchored-review"],
            "status": "resolved_by_construction",
            "fresh_objection_ids": [],
        }
    ]


def test_cross_round_mapping_links_persisting_objection_and_marks_vanished_one():
    persisting = harness._objection(
        "approach", objection_id="approach:old", anchors=["decision:anchored-review"]
    )
    vanished = harness._objection(
        "scope", objection_id="scope:old", anchors=["patch_plan:anchored-review"]
    )
    fresh = review._parse_objection(
        harness._objection(
            "approach", objection_id="approach:fresh", anchors=["decision:anchored-review", "problem"]
        )
    )

    mapping = review._cross_round_objection_mapping([vanished, persisting], [fresh])

    assert mapping == [
        {
            "prior_objection_id": "approach:old",
            "axis": "approach",
            "prior_anchor_node_ids": ["decision:anchored-review"],
            "status": "unresolved",
            "fresh_objection_ids": ["approach:fresh"],
        },
        {
            "prior_objection_id": "scope:old",
            "axis": "scope",
            "prior_anchor_node_ids": ["patch_plan:anchored-review"],
            "status": "resolved_by_construction",
            "fresh_objection_ids": [],
        },
    ]
    assert mapping == review._cross_round_objection_mapping([persisting, vanished], [fresh])


def test_round_one_review_prompt_keeps_the_full_tree_payload():
    prompt = review._review_prompt(
        review.DIMENSIONS[0],
        harness._patch_series_view(),
        harness._approach_tree(),
        {"problem"},
        [],
        "",
    )
    assert "Technical Approach:" in prompt
    assert "scoped to the revision delta" not in prompt


def test_fresh_shard_keeps_requester_constraints_as_part_of_the_body():
    patch_series = harness._patch_series_view()
    requester_constraint = "requester-policy: keep the public command name stable"
    patch_series["constraints_assumptions"] = [requester_constraint]

    prompt = review._review_prompt(
        review.DIMENSIONS[0],
        patch_series,
        harness._approach_tree(),
        review._collect_node_ids(harness._approach_tree()),
        [],
        "",
        shard_scope=review._round_one_shards(harness._approach_tree())[0],
    )

    assert requester_constraint in prompt


# ---------------------------------------------------------------------------
# Component C: objection lifecycle transitions
# ---------------------------------------------------------------------------


def _replaced_candidate_tree() -> dict[str, Any]:
    tree = harness._approach_tree()
    question = tree["problem"]["question"]
    question["candidates"] = [
        {"id": "candidate:fresh", "summary": "Fresh replacement approach.",
         "replaces": "candidate:status-quo", "replacement_reason": "Review invalidated the old shell."},
        {"id": "candidate:anchored-review", "summary": "Use anchored objections."},
    ]
    return tree


def _init_replaced_repo(tmp_path: Path) -> None:
    harness._init_repo(tmp_path)
    harness._git(tmp_path, "checkout", RFC_BRANCH)
    (tmp_path / "technical-approach-plan.json").write_text(
        json.dumps(_replaced_candidate_tree()) + "\n", encoding="utf-8"
    )
    harness._git(tmp_path, "add", "technical-approach-plan.json")
    harness._git(tmp_path, "commit", "-m", "patch_series v2: replaced candidate")
    harness._git(tmp_path, "checkout", "main")


def _superseded_fixture(tmp_path: Path) -> dict[str, Any]:
    _init_replaced_repo(tmp_path)
    objection = harness._objection(
        "approach",
        objection_id="approach:old-shell",
        anchors=["candidate:status-quo"],
        claim="The old shell candidate cannot support anchored review.",
    )
    _write_round_record(tmp_path, 1, objections=[objection])
    _write_delta(tmp_path, 1, {
        "added_node_ids": ["candidate:fresh"],
        "pruned_node_ids": ["candidate:status-quo"],
        "node_replacements": [{
            "replaced_node_id": "candidate:status-quo",
            "successor_node_id": "candidate:fresh",
            "reason": "Review invalidated the old shell.",
        }],
        "objection_responses": [{
            "objection_id": "approach:old-shell",
            "classification": "superseded_by_replacement",
            "anchor_node_ids": ["candidate:status-quo"],
            "successor_node_ids": ["candidate:fresh"],
        }],
    })
    return objection


def test_replaced_anchor_concern_resolves_by_construction_on_fresh_paper(tmp_path, monkeypatch):
    objection = _superseded_fixture(tmp_path)
    harness._patch_reference(monkeypatch)
    prompts: list[tuple[str, str]] = []
    harness._install_codex_fake(monkeypatch, _quiet_handler(prompts))

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    # The old dialogue is absent and, with nothing fresh raised, the round
    # records a construction resolution instead of carrying a stale objection.
    assert result.status == "direction-ok"
    record = harness._committed_round_record(tmp_path, result)
    assert record["objections"] == []
    assert record["cross_round_objection_mapping"][0]["status"] == "resolved_by_construction"
    successor_prompt = next(
        prompt for kind, prompt in prompts
        if kind == "reviewer" and "Fresh replacement approach." in prompt
    )
    assert objection["claim"] not in successor_prompt
    assert '"status": "superseded"' not in successor_prompt


def test_evidenced_consolidation_nak_is_judged_from_the_blank_round(tmp_path, monkeypatch):
    _superseded_fixture(tmp_path)
    harness._patch_reference(monkeypatch)
    prompts: list[tuple[str, str]] = []
    nak_payload = harness._aufheben_payload("nak", [], nak_reason="Direction is unsuitable.")
    harness._install_codex_fake(monkeypatch, _quiet_handler(prompts, aufheben_payload=nak_payload))

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    # No stale superseded objection is injected into the current objection set.
    # An independently evidenced consolidation NAK with no objections remains a
    # genuine judgment of this blank round.
    assert result.status == "nak"
    record = harness._committed_round_record(tmp_path, result)
    assert record["verdict"] == "nak"
    assert record["consolidation_raw_verdict"] == "nak"
    assert record["cross_round_objection_mapping"][0]["status"] == "resolved_by_construction"


def test_resolved_objection_expires_when_its_anchor_changes(tmp_path, monkeypatch):
    harness._init_repo(tmp_path)
    resolved_changed = {
        **harness._objection("approach", objection_id="approach:settled", anchors=["decision:anchored-review"]),
        "status": "resolved",
    }
    resolved_untouched = {
        **harness._objection("scope", objection_id="scope:settled", anchors=["prior_art:lkml"]),
        "status": "resolved",
    }
    _write_round_record(tmp_path, 1, objections=[], resolved_objections=[resolved_changed, resolved_untouched])
    _write_delta(tmp_path, 1, {"changed_node_ids": ["decision:anchored-review"]})

    continuity = review._round_continuity(
        tmp_path, RFC_BRANCH, RFC_ID, harness._approach_tree(), 2
    )

    assert continuity is not None
    # Canon: GitHub stale-approval dismissal / kernel tag removal on substantial
    # change — the changed-anchor resolution expires and re-enters review scope.
    expired = [entry for entry in continuity["unresolved_objections"] if entry.get("resolution_expired")]
    assert [entry["objection_id"] for entry in expired] == ["approach:settled"]
    assert expired[0]["status"] == "open"
    kept = [entry["objection_id"] for entry in continuity["prior_resolved_objections"]]
    assert kept == ["scope:settled"]


def test_round_without_revision_delta_uses_the_same_fresh_shards(tmp_path, monkeypatch):
    harness._init_repo(tmp_path)
    harness._patch_reference(monkeypatch)
    _write_round_record(tmp_path, 1, objections=[harness._objection("approach")])
    prompts: list[tuple[str, str]] = []
    harness._install_codex_fake(monkeypatch, _quiet_handler(prompts))

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    assert result.status == "direction-ok"
    prompt = next(prompt for kind, prompt in prompts if kind == "reviewer")
    assert "The current paper is sharded by step subtree" in prompt
    assert "scoped to the revision delta" not in prompt
    assert harness._objection("approach")["claim"] not in prompt


# ---------------------------------------------------------------------------
# Brief 20 component 1: round-1 sharding (window size, never sharpness)
# ---------------------------------------------------------------------------


def test_round_one_shards_cover_every_node_exactly_once_as_primary():
    approach = harness._approach_tree()
    shards = review._round_one_shards(approach)

    primaries = [node_id for shard in shards for node_id in shard["primary_node_ids"]]
    assert sorted(primaries) == sorted(review._collect_node_ids(approach))
    assert len(primaries) == len(set(primaries)), "a node must be primary in exactly one shard"
    # Determinism.
    assert shards == review._round_one_shards(approach)


def test_round_one_review_is_sharded_with_small_windows_and_single_consolidation(tmp_path, monkeypatch):
    harness._init_repo(tmp_path)
    harness._patch_reference(monkeypatch)
    prompts: list[tuple[str, str]] = []
    harness._install_codex_fake(monkeypatch, _quiet_handler(prompts))

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    assert result.status == "direction-ok"
    reviewer_prompts = [prompt for kind, prompt in prompts if kind == "reviewer"]
    shard_count = len(review._round_one_shards(harness._approach_tree()))
    assert shard_count > 1
    assert len(reviewer_prompts) == shard_count * len(review.DIMENSIONS)
    # Consolidation still runs exactly once per round over the merged set.
    assert len([kind for kind, _ in prompts if kind == "aufheben"]) == 1
    # The charter is byte-identical in every shard window: full sharpness, no
    # leniency language anywhere in the shard framing.
    for prompt in reviewer_prompts:
        assert "Blocking objections need concrete impact." in prompt
        assert "sharded by step subtree" in prompt
    # Window check on the decision shard: decision body and its 1-hop neighbor
    # (the selected candidate) are present; other subtrees are id-manifest only.
    decision_prompt = next(prompt for prompt in reviewer_prompts if "covers the decision subtree" in prompt)
    assert '"selected_candidate_id": "candidate:anchored-review"' in decision_prompt
    assert "Use anchored objections." in decision_prompt
    assert "LKML patch series threads use anchored replies." not in decision_prompt
    prior_art_prompt = next(prompt for prompt in reviewer_prompts if "covers the prior_art subtree" in prompt)
    assert "LKML patch series threads use anchored replies." in prior_art_prompt


def test_shard_objections_merge_through_the_existing_dedupe(tmp_path, monkeypatch):
    harness._init_repo(tmp_path)
    harness._patch_reference(monkeypatch)
    repeated = harness._objection("approach", anchors=["decision:anchored-review"])

    def handler(repo, prompt, output_schema):
        kind = harness._schema_kind(output_schema)
        if kind == "aufheben":
            return harness._aufheben_payload("needs_revision"), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        # The approach dimension raises the SAME objection from every shard.
        return harness._axis_payload(axis, [dict(repeated)] if axis == "approach" else []), 0

    harness._install_codex_fake(monkeypatch, handler)

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    assert result.status == "needs_revision"
    record = harness._committed_round_record(tmp_path, result)
    matching = [entry for entry in record["objections"] if entry["claim"] == repeated["claim"]]
    assert len(matching) == 1


# ---------------------------------------------------------------------------
# Brief 20 component 2: deterministic unchanged-node patrol in rounds N>1
# ---------------------------------------------------------------------------


def test_patrol_slice_is_deterministic_and_walks_the_whole_set():
    ids = [f"node:{index:02d}" for index in range(20)]

    assert review._patrol_slice(ids, 2) == review._patrol_slice(list(reversed(ids)), 2)
    assert review._patrol_slice(ids, 3) == review._patrol_slice(ids, 3)
    # Consecutive rounds are disjoint until wraparound (20 ids, K=8: rounds 2 and 3).
    round_two = set(review._patrol_slice(ids, 2))
    round_three = set(review._patrol_slice(ids, 3))
    assert round_two and round_three
    assert not (round_two & round_three)
    # Over successive rounds the patrol covers the entire unchanged set.
    walked: set[str] = set()
    for round_number in range(2, 2 + 5):
        walked.update(review._patrol_slice(ids, round_number))
    assert walked == set(ids)
    # Degenerate inputs.
    assert review._patrol_slice([], 5) == []
    assert review._patrol_slice(ids, 1) == []
    small = ["b", "a"]
    assert review._patrol_slice(small, 2) == ["a", "b"]


def test_round_n_patrol_nodes_enter_scope_and_leave_the_manifest(tmp_path, monkeypatch):
    harness._init_repo(tmp_path)
    objection = harness._objection("approach", anchors=["decision:anchored-review"])
    _write_round_record(tmp_path, 1, objections=[objection])
    _write_delta(tmp_path, 1, {
        "changed_node_ids": ["decision:anchored-review"],
        "objection_responses": [{
            "objection_id": objection["objection_id"],
            "classification": "addressed_by_change",
            "anchor_node_ids": ["decision:anchored-review"],
            "successor_node_ids": [],
        }],
    })

    continuity = review._round_continuity(tmp_path, RFC_BRANCH, RFC_ID, harness._approach_tree(), 2)

    assert continuity is not None
    # Deterministic slice from the committed round number, no randomness.
    delta_scope = review._delta_review_scope(harness._approach_tree(), continuity["delta"])
    expected = review._patrol_slice(delta_scope["unchanged_node_ids"], 2)
    assert continuity["patrol_node_ids"] == expected
    assert expected, "the fixture has unchanged nodes to patrol"
    # Patrol bodies are present and patrolled ids leave the not-re-sent manifest.
    assert continuity["patrol_nodes"]
    assert not set(continuity["patrol_node_ids"]) & set(continuity["unchanged_node_ids"])
    # The framing is factual and the nodes are reviewable like any other.
    text = review._revision_scope_text(continuity)
    assert "periodic re-review" in text
    assert "reviewable for new objections like any node above" in text
