"""Tests for the intake sufficiency refiner (ADR-0016 D1b).

The carrier (untrusted) PROPOSES the four named fields; the deterministic kernel DECIDES sufficiency by
checking each is named. Sufficient iff all of outcome/success_condition/negative_control/owner are non-empty.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import goal_refiner


def _carrier(payload):
    return lambda _p: json.dumps(payload)


def test_sufficient_when_all_four_named():
    v = goal_refiner.refine("g", {}, _carrier({
        "outcome": "the chat submit drives the recovery visual",
        "success_condition": "a frozen stage shows recovery, a green run shows delivery",
        "negative_control": "a frozen stage that shows the delivery visual instead",
        "owner": "the steerer",
        "intent": "restore the visual wiring",
    }))
    assert v["sufficient"] is True, v
    assert v["missing"] == []
    assert v["structured"]["outcome"]


def test_underdetermined_when_nothing_nameable():
    v = goal_refiner.refine("make it nice", {}, _carrier({
        "outcome": "", "success_condition": "", "negative_control": "", "owner": "", "intent": "",
    }))
    assert v["sufficient"] is False
    assert set(v["missing"]) == set(goal_refiner.REQUIRED_FIELDS)


def test_partial_lists_only_missing_and_keeps_what_was_named():
    v = goal_refiner.refine("g", {}, _carrier({
        "outcome": "X happens", "success_condition": "X is observed",
        "negative_control": "", "owner": "",
    }))
    assert v["sufficient"] is False
    assert set(v["missing"]) == {"negative_control", "owner"}
    # what WAS nameable is still returned, so a consumer can pre-complete the rest (ADR-0016 D1b)
    assert v["structured"]["outcome"] == "X happens"
    assert v["structured"]["success_condition"] == "X is observed"


def test_fails_closed_on_broken_carrier():
    def boom(_p):
        raise RuntimeError("carrier down")
    v = goal_refiner.refine("g", {}, boom)
    assert v["sufficient"] is False    # a broken refiner must never wave a goal through (ADR-0011)
    assert v.get("error") == "refiner_failed"


def test_non_json_carrier_is_insufficient():
    v = goal_refiner.refine("g", {}, lambda _p: "not json at all")
    assert v["sufficient"] is False


def test_whitespace_only_field_is_not_named():
    v = goal_refiner.refine("g", {}, _carrier({
        "outcome": "   ", "success_condition": "ok", "negative_control": "ok", "owner": "ok",
    }))
    assert v["sufficient"] is False
    assert v["missing"] == ["outcome"]


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print("ok", _name)
    print("all goal_refiner tests passed")
