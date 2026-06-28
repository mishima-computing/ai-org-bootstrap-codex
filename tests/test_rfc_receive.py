from __future__ import annotations

import json

import pytest

from ai_org.rfc.receive import RFC, receive


def test_receive_loads_rfc_from_dict():
    rfc = receive(
        {
            "title": "Manual intake",
            "problem": "RFCs need to be supplied by hand for now.",
            "proposed_change": "Map a manual dict into an RFC dataclass.",
            "interface_sketch": "receive(source)",
            "notes": "Ignore unrelated fields.",
            "extra": "ignored",
        }
    )

    assert rfc == RFC(
        title="Manual intake",
        problem="RFCs need to be supplied by hand for now.",
        proposed_change="Map a manual dict into an RFC dataclass.",
        interface_sketch="receive(source)",
        notes="Ignore unrelated fields.",
    )


def test_receive_loads_rfc_from_json_file(tmp_path):
    path = tmp_path / "rfc.json"
    path.write_text(
        json.dumps(
            {
                "title": "JSON intake",
                "problem": "The manual RFC is stored on disk.",
                "proposed_change": "Load JSON into the RFC dataclass.",
            }
        ),
        encoding="utf-8",
    )

    rfc = receive(path)

    assert rfc == RFC(
        title="JSON intake",
        problem="The manual RFC is stored on disk.",
        proposed_change="Load JSON into the RFC dataclass.",
    )


def test_receive_missing_required_field_raises_clear_error():
    with pytest.raises(ValueError, match="missing required field\\(s\\): proposed_change"):
        receive(
            {
                "title": "Incomplete",
                "problem": "Required validation should fail.",
            }
        )
