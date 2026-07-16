"""The committed patch-author contracts for patch-author contribution branches.

Boundary-needs-an-interface: a cross-clone patch author that does not run this
repository's implement.py must still learn the implementation-result contract
from the series branch itself, not from a prompt it never saw. Canonical CUE is
committed at promotion; this JSON Schema builder is a temporary compatibility
projection used by the harness after it validates that authority.

These schemas are not durable Git authority and are never sent to Codex.
"""
from __future__ import annotations

from typing import Any


IMPLEMENTATION_RESULT_CONTRACT_PATH = "contributor-contract/implementation-result.cue"
# Compatibility name: callers locate the committed CUE contract through this
# symbol; ``implementation_result_schema`` is only a temporary projection.
IMPLEMENTATION_RESULT_SCHEMA_PATH = IMPLEMENTATION_RESULT_CONTRACT_PATH


def implementation_result_schema() -> dict[str, Any]:
    """Return the schema for the implementation-result.json a contribution must carry.

    Probe-A contract gaps closed here (brief 23 addendum, all committed WITH
    the schema so the contract itself states them — x-* keys are annotation
    keywords, ignored by validators, binding as documentation):
      #1 x-committed-location: the exact result FILENAME and where it lives.
      #2 x-node-key-convention: how node_key is derived.
      #3 x-computed-by: the harness (org toolchain) computes
         acknowledged_patchwork_checks from engine facts; a model never
         authors this artifact.
    """
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "implementation-result",
        "description": (
            "Committed at the repository root of a contribution branch. The"
            " acceptance judge verifies every field against engine-computed"
            " facts; fabricated or stale acknowledgements are rejected."
        ),
        "x-committed-location": (
            "implementation-result.cue at the repository ROOT of the"
            " contribution branch (ai-org/contrib/*), committed as its own"
            " commit after every patch-plan item commit."
        ),
        "x-node-key-convention": (
            "node_key is 'root' for a root series; for a nested leaf it is the"
            " child_key — the last path segment of the node's sub/<child_key>/"
            " directory on the series branch (matching the network manifest's"
            " child_key field)."
        ),
        "x-computed-by": (
            "The patch-author harness (the org toolchain) computes this"
            " artifact deterministically from computed_patchwork_check_facts;"
            " acknowledged_patchwork_checks echoes name -> current_value"
            " exactly. Models never write or edit this file."
        ),
        "x-questions-the-patch-must-answer": (
            "When the series branch carries questions-the-patch-must-answer.cue"
            " (review-accepted questions the patch MUST answer), this artifact"
            " carries must_answer_outcomes: one outcome object per objection_id."
            " The acceptance judge fails closed when any question lacks an"
            " outcome. A cold author discovers the obligation from the branch"
            " alone: read that file next to this contract."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": ["patch_series_branch", "node_key", "acknowledged_patchwork_checks"],
        "properties": {
            "patch_series_branch": {
                "type": "string",
                "description": "The exact ai-org/patch-series/* branch this contribution implements.",
            },
            "node_key": {
                "type": "string",
                "description": "The network node key ('root' for a root series, else the child_key).",
            },
            "acknowledged_patchwork_checks": {
                "type": "object",
                "additionalProperties": {
                    "type": ["boolean", "number", "string", "null"],
                },
                "description": (
                    "Exact echo of the engine-computed patchwork check facts"
                    " (name -> current_value) the implementation was built against."
                ),
            },
            "must_answer_outcomes": {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "required": ["outcome"],
                    "properties": {"outcome": {"type": "string", "minLength": 1}},
                },
                "description": (
                    "One outcome object per objection_id from"
                    " questions-the-patch-must-answer.cue on the series branch:"
                    " the executed check's result, and the engaged contingency"
                    " when the check failed. Required whenever that file lists"
                    " questions; the acceptance judge fails closed without it."
                ),
            },
        },
    }
