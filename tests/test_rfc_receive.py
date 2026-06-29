from __future__ import annotations

import json

import pytest

from ai_org.rfc.receive import COMMON_8_FIELDS, REQUEST_SCHEMA, receive


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
