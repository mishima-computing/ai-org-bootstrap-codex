"""Brief 29: schema <-> parser parity for every targeted-revision step.

Memento: the model honored the schema and the engine rejected it — schema and
parser are ONE CONTRACT, and drift between them converts correct output into a
permanent_rejection (run 4 v3 reform, validator receive.revise_risks rejected
"invalid fields" twice for output matching the committed revise-risks schema
exactly; fingerprint f3ca460972fdd57e, 2026-07-05; the fingerprint law then
CORRECTLY declared the repeat permanent and killed the reform). This walker
generates a schema-conforming instance for EVERY targeted step's schema and
asserts the step's own parser ACCEPTS it — any future drift in any step is a
red test here, never a live permanent_rejection.
"""
from __future__ import annotations

import json
from typing import Any

from ai_org.patchwork_queue import receive as receive_module

import test_patch_series_receive as receive_harness

# Per-step plans whose target ids the filled instances address.
_PLANS: dict[str, dict[str, Any]] = {
    "constraints": {
        "step": "constraints",
        "target_ids": ["constraint:hard:1"],
        "objections": [],
        "open_questions": [],
        "neighborhood_ids": [],
        "manifest_ids": [],
        "target_bodies": {},
        "neighborhood_bodies": {},
        "out_of_step_anchor_bodies": {},
    },
    "prior_art": {
        "step": "prior_art",
        "target_ids": ["prior_art:pattern-target"],
        "objections": [],
        "open_questions": [],
        "neighborhood_ids": [],
        "manifest_ids": [],
        "target_bodies": {},
        "neighborhood_bodies": {},
        "out_of_step_anchor_bodies": {},
    },
    "candidates": {
        "step": "candidates",
        "target_ids": ["cand_target"],
        "objections": [],
        "open_questions": [],
        "neighborhood_ids": [],
        "manifest_ids": [],
        "target_bodies": {},
        "neighborhood_bodies": {},
        "out_of_step_anchor_bodies": {},
    },
    "risks": {
        "step": "risks",
        "target_ids": ["risk:target"],
        "objections": [],
        "open_questions": [],
        "neighborhood_ids": [],
        "manifest_ids": [],
        "target_bodies": {},
        "neighborhood_bodies": {},
        "out_of_step_anchor_bodies": {},
    },
}

# The step's own fragment collection (filled with one conforming item; every
# other top-level array stays empty — an empty revision is a valid no-op).
_PRIMARY_COLLECTION = {
    "constraints": "hard_constraints",
    "prior_art": "patterns",
    "candidates": "candidates",
    "risks": "risks",
}

# Field-name value overrides so the SCHEMA-conforming instance also satisfies
# the parser's target-addressing and semantic lints (those are prompt+parser
# contract, not schema): target ids address the plan; declarations stay empty
# (a filled replaces would have to name a target); engine/platform pick real
# product/runtime values the stack lint accepts.
_VALUE_OVERRIDES = {
    "constraints": {"id": "constraint:hard:1", "replaces": "", "replacement_reason": ""},
    "prior_art": {"name": "Pattern Target", "replaces": "", "replacement_reason": ""},
    "candidates": {"id": "cand_target", "replaces": "", "replacement_reason": "", "engine": "Godot", "platform": "browser"},
    "risks": {"id": "risk:target", "replaces": "", "replacement_reason": "", "target_id": "decision:candidate:one"},
}


def _fill(fragment: dict[str, Any], overrides: dict[str, str], field_name: str = "") -> Any:
    if "enum" in fragment:
        return fragment["enum"][0]
    kind = fragment.get("type")
    if kind == "object":
        return {
            field: _fill(fragment["properties"][field], overrides, field)
            for field in fragment.get("required", [])
        }
    if kind == "array":
        return [_fill(fragment["items"], overrides, field_name)]
    if kind == "string":
        return overrides.get(field_name, "Filled for parity.")
    if kind == "boolean":
        return False
    return None


def _schema_conforming_instance(step: str) -> dict[str, Any]:
    schema = receive_module._targeted_revision_schema(step, _PLANS[step])
    overrides = _VALUE_OVERRIDES[step]
    instance: dict[str, Any] = {}
    for field in schema["required"]:
        if field == _PRIMARY_COLLECTION[step]:
            instance[field] = [_fill(schema["properties"][field]["items"], overrides, field)]
        else:
            instance[field] = []
    return instance


def test_every_targeted_step_schema_required_envelope_is_parser_accepted():
    # Layer 1: the pure envelope — every schema-REQUIRED top-level key present
    # with empty collections is a valid no-op revision. This exact assertion
    # would have caught the live risks drift (the envelope keys the schema
    # required were rejected as "invalid fields").
    for step in receive_module._TARGETED_REVISION_STEPS:
        schema = receive_module._targeted_revision_schema(step, _PLANS[step])
        envelope = {field: [] for field in schema["required"]}
        parsed = receive_module._parse_targeted_fragments(step, json.dumps(envelope), _PLANS[step])
        assert parsed.get("ok", True), f"{step}: parser rejected its own schema envelope: {parsed.get('error')}"
        assert parsed["fragments"] == []


def test_every_targeted_step_accepts_a_schema_conforming_filled_instance():
    # Layer 2: one filled item per step, generated FROM the schema (enum-first,
    # required-only), addressing the plan's target — the parser must accept it.
    for step in receive_module._TARGETED_REVISION_STEPS:
        instance = _schema_conforming_instance(step)
        parsed = receive_module._parse_targeted_fragments(step, json.dumps(instance), _PLANS[step])
        assert parsed.get("ok", True), f"{step}: parser rejected schema-conforming output: {parsed.get('error')}"
        assert len(parsed["fragments"]) == 1, step


def test_risks_revision_with_full_envelope_completes_a_reform(tmp_path, monkeypatch):
    receive_harness._install_historical_reform_preparation(monkeypatch)
    # The exact run-4 shape end-to-end: a schema-honoring risks revision (with
    # the required envelope keys AND per-item declaration fields) is accepted
    # and the reform completes.
    repo = receive_harness._init_repo(tmp_path)
    series_branch = "ai-org/patch-series/reviewable-patch_series"
    tree = receive_harness._reform_approach_tree("old slice")
    tree["problem"]["question"]["decision"]["risks"] = [
        {"id": f"risk:{name}", "risk": f"Risk {name}.", "mitigation": f"Mitigate {name}.",
         "attaches_to": "decision", "target_id": "decision:candidate:one"}
        for name in ("alpha", "beta", "gamma")
    ]
    tree = receive_module._assemble_from_components(receive_module._approach_components(tree))
    view = receive_harness._patch_series_view("Reviewable patch series")
    receive_harness._git(repo, "checkout", "-B", series_branch, "main")
    (repo / "patch-series-cover-letter.json").write_text(json.dumps(view) + "\n", encoding="utf-8")
    (repo / "technical-approach-plan.json").write_text(json.dumps(tree) + "\n", encoding="utf-8")
    receive_harness._git(repo, "add", "-A")
    receive_harness._git(repo, "commit", "-m", "initial patch_series")
    receive_harness._git(repo, "commit", "--allow-empty", "-m", "patch_series: needs-revision round 1")
    receive_harness._git(repo, "checkout", "main")
    receive_harness._write_review_round(
        repo, "reviewable-patch_series", receive_harness._blocking_objection("risk:beta")
    )
    calls: list[dict[str, Any]] = []

    def fake_run_json(repo_path, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "raw": json.dumps({
            "risks": [{
                "id": "risk:beta",
                "risk": "Risk beta, revised with concrete blast radius.",
                "mitigation": "Mitigate beta with the bounded probe.",
                "attaches_to": "decision",
                "target_id": "decision:candidate:one",
                "replaces": "",
                "replacement_reason": "",
            }],
            "deferral_proposals": [],
            "closed_questions": [],
        })}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module.reform_patch_series(repo, "reviewable-patch_series")

    assert result["status"] == "reformed"
    assert [call["failure_label"] for call in calls] == ["Codex targeted risks revision"]
    revised_tree = json.loads(receive_harness._git(repo, "show", f"{series_branch}:technical-approach-plan.json"))
    revised_risks = {item["id"]: item for item in revised_tree["problem"]["question"]["decision"]["risks"]}
    assert revised_risks["risk:beta"]["risk"] == "Risk beta, revised with concrete blast radius."
    before_risks = {item["id"]: item for item in tree["problem"]["question"]["decision"]["risks"]}
    assert revised_risks["risk:alpha"] == before_risks["risk:alpha"]
    assert revised_risks["risk:gamma"] == before_risks["risk:gamma"]
