"""Brief 19 receive-side tests: node identity contract, delta ledger, dangling lint.

Three live incidents, one disease (LLM-renamed node ids x non-tracking references):
brief15 (evaluations x candidates), pomodoro round-2 (cross_links x renamed prior_art
ids), DQ round-6 (dangling objection anchors -> unresolvable -> NAK).
"""
from __future__ import annotations

import json
from typing import Any

from ai_org.patchwork_queue import receive as receive_module
from ai_org.patchwork_queue import review as review_module
from ai_org.patchwork_queue.field_registry import WORK_ORDER_VIEW_FIELDS


def _approach_tree(candidate_id: str, *, replaces: str | None = None, slice_text: str = "old slice",
                   constraints_hard: list[dict[str, str]] | None = None,
                   extra_cross_links: list[dict[str, str]] | None = None) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "id": candidate_id,
        "summary": "Approach summary.",
        "draws_on": ["Some prior art referenced only by prose name"],
        "evaluation": {"id": f"evaluation:{candidate_id}", "candidate_id": candidate_id, "scores": {}},
    }
    if replaces:
        candidate["replaces"] = replaces
        candidate["replacement_reason"] = "Direction change demanded by review."
    hard = constraints_hard if constraints_hard is not None else [{"id": "constraint:hard:1", "text": "Same branch v2."}]
    return {
        "problem": {
            "id": "problem",
            "problem": "Continuity fixture.",
            "goals": [{"id": "goal:1", "criterion": "Reviewer can follow revisions."}],
            "constraints": {"hard": hard, "soft": []},
            "prior_art": [{"id": "prior_art:lkml", "name": "LKML v2", "traces_to": ["normalized_problem.success_criteria[0]"]}],
            "question": {
                "id": "question:approach",
                "candidates": [candidate],
                "decision": {
                    "id": f"decision:{candidate_id}",
                    "selected_candidate_id": candidate_id,
                    "implementation": {
                        "id": f"implementation:{candidate_id}",
                        "patch_plan": {"id": f"patch_plan:{candidate_id}", "first_safe_slice": slice_text},
                        "risks": [],
                    },
                    "risks": [],
                },
            },
        },
        "cross_links": [
            {"from": f"evaluation:{candidate_id}", "to": candidate_id, "type": "derived_from"},
            {"from": "argument:synthetic:1", "to": candidate_id, "type": "supports"},
            *(extra_cross_links or []),
        ],
    }


def _objection(objection_id: str, anchors: list[str]) -> dict[str, Any]:
    return {"objection_id": objection_id, "anchor_node_ids": anchors, "type": "blocking", "status": "open"}


# ---------------------------------------------------------------------------
# Component A: node identity contract
# ---------------------------------------------------------------------------


def test_candidate_prompt_carries_revision_contract_only_when_revising():
    baseline = {"previous_nodes": [{"id": "candidate:one", "summary": "Previous approach."}]}
    revised = receive_module._generate_candidate_prompt(
        {}, {}, {}, "repo_native", [], None, None, None, revision=baseline
    )
    assert "REVISION CONTRACT" in revised
    assert "candidate:one" in revised
    assert "never constrains which technical choice to make" in revised
    assert "declared replaced" in revised

    founding = receive_module._generate_candidate_prompt({}, {}, {}, "repo_native", [], None, None, None)
    assert "REVISION CONTRACT" not in founding


def test_prior_art_prompt_revision_contract_states_the_name_derivation_rule(tmp_path):
    view = {field: "" for field in WORK_ORDER_VIEW_FIELDS}
    baseline = {"previous_nodes": [{"id": "prior_art:lkml", "name": "LKML v2"}]}
    prompt = receive_module._prior_art_map_prompt(view, tmp_path, [], [], None, None, None, revision=baseline)
    assert "REVISION CONTRACT" in prompt
    assert "prior_art:<slugified-name>" in prompt
    assert "prior_art:lkml" in prompt
    assert "REVISION CONTRACT" not in receive_module._prior_art_map_prompt(view, tmp_path, [], [])


def test_surface_risks_prompt_carries_revision_contract():
    baseline = {"previous_nodes": [{"id": "risk:old", "risk": "Previous risk."}]}
    prompt = receive_module._surface_risks_prompt({}, {}, {}, {}, None, None, None, None, revision=baseline)
    assert "REVISION CONTRACT" in prompt
    assert "risk:old" in prompt


def test_revision_schemas_add_declaration_fields_without_touching_formation_builders():
    for revision_schema, node_items in (
        (receive_module._revision_candidate_schema(), None),
        (receive_module._revision_prior_art_schema(), ("patterns",)),
        (receive_module._revision_surface_risks_schema(["decision:x"]), ("risks",)),
    ):
        target = revision_schema
        if node_items:
            target = revision_schema["properties"][node_items[0]]["items"]
        assert "replaces" in target["properties"]
        assert "replacement_reason" in target["properties"]
        assert "replaces" in target["required"]
        assert "replacement_reason" in target["required"]

    # Formation builders are byte-identical (snapshot guard also enforces this).
    assert "replaces" not in receive_module.build_candidate_approach_schema()["properties"]
    assert "replaces" not in receive_module.build_prior_art_map_schema()["properties"]["patterns"]["items"]["properties"]


def _candidate_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "pixi_dom_jrpg",
        "name": "Pixi DOM",
        "kind": "repo_native",
        "summary": "Render the JRPG with PixiJS over DOM overlays.",
        "stack_requirement": {
            "build_strategy": "framework_based",
            "engine": "",
            "framework": "PixiJS",
            "language": "TypeScript",
            "platform": "browser",
            "authoring_model": "text_first",
            "verification_model": "headless_ci",
        },
        "first_proof_moment": {
            "user_actions": ["Open the town screen."],
            "named_content": [{"name": "Town", "kind": "location"}],
            "win_or_progress_condition": "The town renders and the hero moves.",
        },
        "core_systems": ["rendering"],
        "draws_on": ["LKML v2"],
    }
    base.update(overrides)
    return base


def test_candidate_parser_preserves_surviving_ids_and_carries_declarations_only_when_declared():
    surviving = receive_module._parse_candidate_approach(json.dumps(_candidate_payload(replaces="", replacement_reason="")))
    assert surviving.get("ok", True)
    assert surviving["id"] == "pixi_dom_jrpg"
    assert "replaces" not in surviving and "replacement_reason" not in surviving

    replacing = receive_module._parse_candidate_approach(
        json.dumps(_candidate_payload(replaces="phaser_jrpg", replacement_reason="Review invalidated Phaser."))
    )
    assert replacing["replaces"] == "phaser_jrpg"
    assert replacing["replacement_reason"] == "Review invalidated Phaser."

    # A declaration without a reason stays visible to the empty-slot lint.
    unexplained = receive_module._parse_candidate_approach(
        json.dumps(_candidate_payload(replaces="phaser_jrpg", replacement_reason=""))
    )
    assert unexplained["replacement_reason"] == ""
    assert any("replacement_reason" in error for error in receive_module._candidate_empty_slot_lints(unexplained))

    malformed = receive_module._parse_candidate_approach(json.dumps(_candidate_payload(replaces=7)))
    assert malformed["ok"] is False
    bogus = receive_module._parse_candidate_approach(json.dumps(_candidate_payload(bogus_field="x")))
    assert bogus["ok"] is False


def test_prior_art_and_risk_parsers_carry_declarations():
    pattern = {
        "name": "LKML v2 reroll",
        "source": {"reference_concept": "kernel review flow", "facet_kind": "none", "where": "kernel docs"},
        "when_applies": "Whenever a series is revised after review.",
        "tradeoffs": {"pros": ["continuity"], "cons": ["discipline cost"]},
        "disposition": {"choice": "adopt", "why": "It fits revision continuity."},
        "traces_to": ["normalized_problem.success_criteria[0]"],
    }
    replacing = dict(pattern)
    replacing["replaces"] = "prior_art:old-name"
    replacing["replacement_reason"] = "Pattern renamed after re-grounding."
    parsed = receive_module._parse_prior_art_map(json.dumps({"patterns": [pattern, dict(pattern), replacing]}))
    assert parsed.get("ok", True)
    assert "replaces" not in parsed["patterns"][0]
    assert parsed["patterns"][2]["replaces"] == "prior_art:old-name"

    risk = {"id": "risk:new", "risk": "Risk.", "mitigation": "Mitigate.", "attaches_to": "decision",
            "target_id": "decision:x", "replaces": "risk:old", "replacement_reason": "Risk restated for new decision."}
    parsed_risks = receive_module._parse_surface_risks(json.dumps({"risks": [risk]}))
    assert parsed_risks.get("ok", True)
    assert parsed_risks["risks"][0]["replaces"] == "risk:old"


def test_reform_steps_thread_the_previous_generation_as_revision_baseline(monkeypatch, tmp_path):
    components = receive_module._approach_components(_approach_tree("candidate:one"))
    seen: dict[str, Any] = {}

    def fake_generate_candidates(*_args, revision=None, **_kwargs):
        seen["candidates"] = revision
        return {"candidates": [{"id": "candidate:one"}]}

    def fake_prior_art(*_args, revision=None, **_kwargs):
        seen["prior_art"] = revision
        return {"patterns": [{"name": "LKML v2"}]}

    def fake_surface_risks(*_args, revision=None, **_kwargs):
        seen["risks"] = revision
        return {"risks": []}

    monkeypatch.setattr(receive_module, "_generate_candidates", fake_generate_candidates)
    monkeypatch.setattr(receive_module, "_build_prior_art_map", fake_prior_art)
    monkeypatch.setattr(receive_module, "_surface_risks", fake_surface_risks)

    receive_module._run_reform_step("candidates", tmp_path, {}, components, {"repo": tmp_path})
    receive_module._run_reform_step("prior_art", tmp_path, {}, components, {"repo": tmp_path})
    receive_module._run_reform_step("risks", tmp_path, {}, components, {"repo": tmp_path})

    assert [node["id"] for node in seen["candidates"]["previous_nodes"]] == ["candidate:one"]
    assert [node["id"] for node in seen["prior_art"]["previous_nodes"]] == ["prior_art:lkml"]
    assert seen["risks"] is None  # no previous risk nodes -> founding behavior


# ---------------------------------------------------------------------------
# Component B: revision delta ledger
# ---------------------------------------------------------------------------


def test_revision_delta_classifies_objection_responses_mechanically():
    previous = _approach_tree("candidate:one")
    revised = _approach_tree("candidate:svelte", replaces="candidate:one", slice_text="new slice")
    objections = [
        _objection("approach:1", ["candidate:one", "decision:candidate:one"]),
        _objection("scope:2", ["patch_plan:candidate:one"]),
        _objection("need:3", ["goal:1"]),
    ]

    delta = receive_module._revision_delta_record("series-x", 2, 3, previous, revised, objections)

    assert delta["review_round"] == 2 and delta["author_version"] == 3
    assert "candidate:one" in delta["pruned_node_ids"]
    assert "candidate:svelte" in delta["added_node_ids"]
    replacement_pairs = {(item["replaced_node_id"], item["successor_node_id"]) for item in delta["node_replacements"]}
    assert ("candidate:one", "candidate:svelte") in replacement_pairs
    # Derived ids follow mechanically: evaluation via the declared replacement,
    # decision/implementation/patch_plan singletons follow the selection.
    for family in ("evaluation:", "decision:", "implementation:", "patch_plan:"):
        assert (f"{family}candidate:one", f"{family}candidate:svelte") in replacement_pairs

    by_id = {item["objection_id"]: item for item in delta["objection_responses"]}
    assert by_id["approach:1"]["classification"] == "superseded_by_replacement"
    assert "candidate:svelte" in by_id["approach:1"]["successor_node_ids"]
    assert by_id["scope:2"]["classification"] == "superseded_by_replacement"
    assert by_id["need:3"]["classification"] == "unaddressed"

    # Determinism.
    assert delta == receive_module._revision_delta_record("series-x", 2, 3, previous, revised, objections)


def test_changed_anchor_classifies_addressed_and_reindexed_family_never_violates():
    previous = _approach_tree("candidate:one")
    revised = _approach_tree("candidate:one", slice_text="revised slice", constraints_hard=[])
    objections = [
        _objection("scope:1", ["patch_plan:candidate:one"]),
        _objection("compat:2", ["constraint:hard:1"]),
    ]

    delta = receive_module._revision_delta_record("series-x", 1, 2, previous, revised, objections)
    by_id = {item["objection_id"]: item for item in delta["objection_responses"]}
    assert by_id["scope:1"]["classification"] == "addressed_by_change"
    # Positional/code-indexed families cannot declare successors: pruning them is
    # classified as change, and the lint never raises undeclared-prune for them.
    assert by_id["compat:2"]["classification"] == "addressed_by_change"
    assert receive_module._revision_continuity_violations(previous, revised, objections) == []


# ---------------------------------------------------------------------------
# Companion: dangling-reference lint
# ---------------------------------------------------------------------------


def test_declared_replacement_flow_passes_the_lint_and_undeclared_prune_fails_closed():
    previous = _approach_tree("candidate:one")
    declared = _approach_tree("candidate:svelte", replaces="candidate:one")
    objections = [_objection("approach:1", ["candidate:one", "decision:candidate:one"])]
    assert receive_module._revision_continuity_violations(previous, declared, objections) == []

    undeclared = _approach_tree("candidate:svelte")
    violations = receive_module._revision_continuity_violations(previous, undeclared, objections)
    assert violations, "undeclared prune must fail closed"
    assert any("undeclared prune" in item and "'candidate:one'" in item and "'approach:1'" in item for item in violations)
    # The moved singleton (decision:) is a derived id and is excused mechanically.
    assert not any("decision:candidate:one" in item for item in violations)


def test_dangling_reference_lint_names_exact_refs_and_never_flags_prose_or_synthetic():
    previous = _approach_tree("candidate:one")
    # The pomodoro round-2 shape: a cross_link pointing at a renamed prior_art id.
    revised = _approach_tree(
        "candidate:one",
        extra_cross_links=[{"from": "candidate:one", "to": "prior_art:renamed-away", "type": "derived_from"}],
    )
    violations = receive_module._revision_continuity_violations(previous, revised, [])
    assert len(violations) == 1
    assert "cross_links.to='prior_art:renamed-away'" in violations[0]
    # Prose fields (draws_on with a non-matching name, traces_to) and synthetic
    # argument:* endpoints are never flagged; a fully consistent tree is clean.
    assert receive_module._revision_continuity_violations(previous, _approach_tree("candidate:one"), []) == []


def test_stale_evaluation_reference_is_caught_by_the_lint():
    # The brief15 shape at the reference level: an evaluation matrix entry that
    # still points at a dead-generation candidate id.
    previous = _approach_tree("candidate:one")
    revised = _approach_tree("candidate:svelte", replaces="candidate:one")
    revised["problem"]["question"]["candidates"][0]["evaluation"]["candidate_id"] = "candidate:ghost"
    violations = receive_module._revision_continuity_violations(previous, revised, [])
    assert any("candidate_id='candidate:ghost'" in item for item in violations)


def test_revision_delta_path_lives_beside_round_records():
    assert review_module.revision_delta_record_path(3) == (
        "patch-series-review-rounds/round-0003-revision-delta.cue"
    )


# ---------------------------------------------------------------------------
# Brief 21: root-held reference remap + owning-step routing for legacy danglings
# ---------------------------------------------------------------------------


def _renamed_prior_art_tree(*, declare: bool, extra_declaration: bool = False) -> dict[str, Any]:
    tree = _approach_tree("candidate:one")
    pattern: dict[str, Any] = {"id": "prior_art:review-reroll", "name": "Review reroll"}
    if declare:
        pattern["replaces"] = "prior_art:lkml"
        pattern["replacement_reason"] = "Pattern renamed across generations."
    tree["problem"]["prior_art"] = [pattern]
    if extra_declaration:
        tree["problem"]["prior_art"].append(
            {"id": "prior_art:second-claimant", "name": "Second claimant",
             "replaces": "prior_art:lkml", "replacement_reason": "Competing claim."}
        )
    # The pomodoro shape: a root-held cross_link (regenerated from candidate
    # draws_on prose at assembly) still points at the previous-generation id.
    tree["cross_links"].append({"from": "candidate:one", "to": "prior_art:lkml", "type": "derived_from"})
    return tree


def test_root_cross_links_remap_through_declared_replacements():
    previous = _approach_tree("candidate:one")
    revised = _renamed_prior_art_tree(declare=True)
    stale_count = len(revised["cross_links"])

    remapped = receive_module._remap_root_reference_ids(previous, revised)

    links = remapped["cross_links"]
    assert len(links) == stale_count, "links are never dropped by the remap"
    assert {"from": "candidate:one", "to": "prior_art:review-reroll", "type": "derived_from"} in links
    assert not any(link.get("to") == "prior_art:lkml" for link in links)
    # Untouched links stay byte-identical.
    assert links[0] == revised["cross_links"][0]
    # The repaired tree passes the continuity gate.
    assert receive_module._revision_continuity_violations(previous, remapped, []) == []


def test_undeclared_or_ambiguous_dead_endpoints_are_left_for_the_lint():
    previous = _approach_tree("candidate:one")

    undeclared = _renamed_prior_art_tree(declare=False)
    assert receive_module._remap_root_reference_ids(previous, undeclared) is undeclared
    violations = receive_module._revision_continuity_violations(previous, undeclared, [])
    assert any("cross_links.to='prior_art:lkml'" in item for item in violations)

    # An AMBIGUOUS declaration (two claimants for the same predecessor): the
    # remap never guesses, so the link stays on the historical id — but it IS a
    # declared replacement, so the ratified lint rule excuses it; both claimants
    # are auditable in the ledger and the dangling id re-feeds the owning step
    # on the next reform.
    ambiguous = _renamed_prior_art_tree(declare=True, extra_declaration=True)
    remapped = receive_module._remap_root_reference_ids(previous, ambiguous)
    assert any(link.get("to") == "prior_art:lkml" for link in remapped["cross_links"])
    assert receive_module._revision_continuity_violations(previous, ambiguous, []) == []


def test_cross_links_is_the_only_root_held_reference_holder():
    # Brief 21 item 3: sweep for root-held id-typed fields. Every reference the
    # walker attributes to the tree root comes from the top-level cross_links
    # array; every other id-typed field lives inside an id-bearing node.
    references = receive_module._id_typed_tree_references(_renamed_prior_art_tree(declare=True))
    root_held = [item for item in references if item["holder"] in ("", "root")]
    assert root_held, "the fixture carries root-held cross_links"
    assert all(item["field"].startswith("cross_links.") for item in root_held)


def test_unresolved_reference_families_route_to_owning_steps():
    tree = _approach_tree("candidate:one", extra_cross_links=[
        {"from": "candidate:one", "to": "prior_art:ghost-pattern", "type": "derived_from"},
        {"from": "risk:ghost", "to": "decision:candidate:one", "type": "mitigates"},
        {"from": "candidate:one", "to": "decision:ghost", "type": "depends_on"},
        {"from": "candidate:one", "to": "constraint:hard:9", "type": "derived_from"},
    ])
    tree["problem"]["question"]["candidates"][0]["evaluation"]["candidate_id"] = "candidate:ghost"

    families = receive_module._unresolved_reference_families(tree)

    assert families["prior_art"] == ["prior_art:ghost-pattern"]
    assert families["risks"] == ["risk:ghost"]
    assert families["candidates"] == ["candidate:ghost"]
    # Derived and reindexed families route nowhere (no declaration mechanism).
    flattened = [target for targets in families.values() for target in targets]
    assert "decision:ghost" not in flattened
    assert "constraint:hard:9" not in flattened


def test_revision_prompts_carry_no_unresolved_reference_scaffolding(tmp_path):
    # Brief 22 amendment (gadget doctrine): initial prompts unchanged, repair
    # deploys reactively by error type. The brief 21 in-prompt escape block was
    # proactive scaffolding inside a saturated prompt and drowned (725KB live
    # prompt, zero declarations twice) — it is gone from every revision prompt.
    baseline = {"previous_nodes": [{"id": "prior_art:review-reroll"}]}
    assert "UNRESOLVED INHERITED REFERENCES" not in receive_module._revision_contract_text(baseline)
    candidate_prompt = receive_module._generate_candidate_prompt(
        {}, {}, {}, "repo_native", [], None, None, None, revision=baseline
    )
    assert "UNRESOLVED INHERITED REFERENCES" not in candidate_prompt
    from ai_org.patchwork_queue.field_registry import WORK_ORDER_VIEW_FIELDS as _fields
    view = {field: "" for field in _fields}
    prior_art_prompt = receive_module._prior_art_map_prompt(view, tmp_path, [], [], None, None, None, revision=baseline)
    assert "UNRESOLVED INHERITED REFERENCES" not in prior_art_prompt
    risks_prompt = receive_module._surface_risks_prompt({}, {}, {}, {}, None, None, None, None, revision=baseline)
    assert "UNRESOLVED INHERITED REFERENCES" not in risks_prompt
    # The step's OWN identity bookkeeping (brief19-A replaces mechanism) stays.
    assert "REVISION CONTRACT" in candidate_prompt


def test_remap_is_identity_on_clean_trees():
    previous = _approach_tree("candidate:one")
    clean = _approach_tree("candidate:one")
    assert receive_module._remap_root_reference_ids(previous, clean) is clean


# ---------------------------------------------------------------------------
# Brief 22: bounded reference-reconciliation repair round (sole legacy path)
# ---------------------------------------------------------------------------


def _install_reconciliation_fake(monkeypatch, raw: str):
    calls: list[dict[str, Any]] = []

    def fake_run_json(repo, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "raw": raw}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)
    return calls


def test_reconciliation_round_maps_identity_in_a_tiny_window(monkeypatch, tmp_path):
    previous = _approach_tree("candidate:one")
    undeclared = _renamed_prior_art_tree(declare=False)
    calls = _install_reconciliation_fake(
        monkeypatch,
        json.dumps(
            {
                "reconciliations": [
                    {
                        "dangling_id": "prior_art:lkml",
                        "successor_id": "prior_art:review-reroll",
                        "no_successor": False,
                        "reason": "Same review-flow concern; the pattern was renamed.",
                    }
                ]
            }
        ),
    )

    result = receive_module._reconcile_dangling_references(tmp_path, previous, undeclared, [])

    assert result is not None
    reconciled_tree, no_successor = result
    assert no_successor == []
    # The identity judgment repaired the tree: remapped root link, gate green.
    assert receive_module._revision_continuity_violations(previous, reconciled_tree, []) == []
    assert any(
        link.get("to") == "prior_art:review-reroll" for link in reconciled_tree["cross_links"]
    )
    # The reconciliation is tree-sticky and auditable in the replacement channel.
    declarations = receive_module._tree_replacement_declarations(reconciled_tree)
    assert {
        "replaced_node_id": "prior_art:lkml",
        "successor_node_id": "prior_art:review-reroll",
        "reason": "Same review-flow concern; the pattern was renamed.",
    } in declarations

    # ONE bounded call, NORMAL effort (identity mapping is judgment, not a slot refill).
    assert len(calls) == 1
    assert "reasoning_effort" not in calls[0]
    assert calls[0]["failure_label"] == "Codex reference reconciliation"
    # The window contains ONLY the dead ids and same-family id/name/summary lines.
    prompt = calls[0]["prompt"]
    assert "prior_art:lkml" in prompt
    assert "Review reroll" in prompt
    assert "Approach summary." not in prompt  # candidate family not involved
    assert '"scores"' not in prompt  # no node bodies
    assert "cross_links" not in prompt  # no tree
    schema = calls[0]["schema"]
    assert schema["properties"]["reconciliations"]["items"]["properties"]["dangling_id"]["enum"] == [
        "prior_art:lkml"
    ]


def test_reconciliation_no_successor_and_invalid_rounds_stay_fail_closed(monkeypatch, tmp_path):
    previous = _approach_tree("candidate:one")
    undeclared = _renamed_prior_art_tree(declare=False)

    calls = _install_reconciliation_fake(
        monkeypatch,
        json.dumps(
            {
                "reconciliations": [
                    {
                        "dangling_id": "prior_art:lkml",
                        "successor_id": "",
                        "no_successor": True,
                        "reason": "No current pattern covers that concern.",
                    }
                ]
            }
        ),
    )
    result = receive_module._reconcile_dangling_references(tmp_path, previous, undeclared, [])
    assert result is not None
    tree, records = result
    assert records == [
        {"dangling_id": "prior_art:lkml", "reason": "No current pattern covers that concern."}
    ]
    # The dangling link stays for the fail-closed lint; nothing is dropped.
    assert receive_module._revision_continuity_violations(previous, tree, [])
    assert len(calls) == 1

    # Partial coverage, invented ids, or cross-family successors fail the round.
    for raw in (
        json.dumps({"reconciliations": []}),
        json.dumps({"reconciliations": [{"dangling_id": "prior_art:lkml", "successor_id": "candidate:one", "no_successor": False, "reason": "Wrong family."}]}),
        json.dumps({"reconciliations": [{"dangling_id": "prior_art:lkml", "successor_id": "prior_art:invented", "no_successor": False, "reason": "Invented."}]}),
        "not json",
    ):
        calls = _install_reconciliation_fake(monkeypatch, raw)
        assert receive_module._reconcile_dangling_references(tmp_path, previous, undeclared, []) is None
        assert len(calls) == 1, "one round only"


def test_reconciliation_skips_codex_when_no_family_can_map(monkeypatch, tmp_path):
    previous = _approach_tree("candidate:one")
    # Dead prior_art reference but the current tree has NO prior_art nodes at all:
    # nothing to map to, no call spent; the lint expresses the failure.
    empty_family = _renamed_prior_art_tree(declare=False)
    empty_family["problem"]["prior_art"] = []
    calls = _install_reconciliation_fake(monkeypatch, "{}")

    assert receive_module._reconcile_dangling_references(tmp_path, previous, empty_family, []) is None
    assert calls == []


def test_revision_continuity_error_has_no_gadget_hint():
    # Auto-repaired site (reconciliation round): deliberately absent from the hint
    # registry, mirroring the decision-step precedent — never double-hint.
    from ai_org import deterministic_structure_gadgets as gadgets

    result = gadgets.match_gadget_hints(
        {"validator_error": "revision continuity violations: dangling node reference: cross_links.to='prior_art:lkml'"}
    )
    assert result == {"ok": True, "hints": []}


# ---------------------------------------------------------------------------
# Brief 24: assembly must not fabricate references from prose provenance
# ---------------------------------------------------------------------------


def _assembly_components(draws_on: list[str], prior_art_patterns: list[dict[str, Any]]) -> dict[str, Any]:
    components = receive_module._approach_components({})
    components["selected"] = {"selected_candidate_id": "candidate:one"}
    components["candidates"] = {
        "candidates": [{"id": "candidate:one", "summary": "Approach summary.", "draws_on": list(draws_on)}]
    }
    components["prior_art"] = {"patterns": prior_art_patterns}
    return components


def test_draws_on_links_emit_only_for_current_nodes_or_lineage():
    prose_note = "Repository inspection: HEAD has no tracked files beyond scaffolding"
    components = _assembly_components(
        [prose_note, "Real Pattern", "Old Name"],
        [
            {"name": "Real Pattern"},
            {"name": "Renamed Pattern", "replaces": "prior_art:old-name",
             "replacement_reason": "Renamed with declaration."},
        ],
    )

    tree = receive_module._assemble_from_components(components)

    links = tree["cross_links"]
    targets = [link["to"] for link in links if link.get("from") == "candidate:one" and link["to"].startswith("prior_art:")]
    # (a) current node id -> link emitted exactly as today.
    assert "prior_art:real-pattern" in targets
    # (b) replacement-map lineage -> link emitted (the root remap carries it).
    assert "prior_art:old-name" in targets
    # Prose provenance -> NO link fabricated; the prose stays verbatim.
    assert not any("repository-inspection" in target for target in targets)
    candidate = tree["problem"]["question"]["candidates"][0]
    assert prose_note in candidate["draws_on"]
    # The reclassification is recorded for the ledger, never silent.
    assert receive_module._prose_provenance_draws_on(tree) == [
        "prior_art:repository-inspection-head-has-no-tracked-files-beyond-scaffolding"
    ]


def test_revision_delta_ledger_records_prose_reclassifications():
    components = _assembly_components(
        ["Local tool availability: python3 present without extra setup"],
        [{"name": "Real Pattern"}],
    )
    tree = receive_module._assemble_from_components(components)

    delta = receive_module._revision_delta_record("series-x", 1, 2, tree, tree, [])

    assert delta["prose_provenance_not_linked"] == [
        "prior_art:local-tool-availability-python3-present-without-extra-setup"
    ]


def test_assembly_fabricates_no_unresolvable_reference_at_all():
    # Adjacent-fabrication pin (brief 24 item 4): the sweep found exactly one
    # prose-to-reference slugification site (draws_on -> prior_art links), now
    # guarded; every other _slug use mints a node's OWN id, never a reference.
    # Functional pin: an assembled tree stuffed with prose in every prose field
    # yields cross_links whose endpoints all resolve (or are synthetic
    # argument:* ids) - assembly cannot invent a reference.
    components = _assembly_components(
        ["Pure prose provenance that matches no pattern"],
        [{"name": "Real Pattern", "when_applies": "Prose condition text.",
          "traces_to": ["normalized_problem.success_criteria[0]"]}],
    )
    components["candidates"]["candidates"][0]["core_systems"] = ["prose system description"]
    tree = receive_module._assemble_from_components(components)

    known = set(receive_module._tree_node_contents(tree))
    for link in tree["cross_links"]:
        for endpoint in (link["from"], link["to"]):
            assert endpoint in known or endpoint.startswith("argument:"), endpoint
