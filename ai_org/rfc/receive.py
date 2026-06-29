# receive.py — the INTAKE GATE: judges whether an incoming REQUEST may become an RFC.
# This is NOT a dumb loader/translator. A request is discussed and can be SENT BACK (差し戻し):
#     request --[receive gate]--> promote to RFC | send back for revision | reject.
# Only requests that PASS this gate become an RFC (ai-org/rfc/<id>), which then goes to rfc/review
# (the direction debate). So the RFC phase has TWO gates, in order:
#     1) receive : can this REQUEST become an RFC?   (this file)
#     2) review  : is the RFC's DIRECTION ok?        (review.py)
# Shape (to match the other stages): git-read the request -> codex judges (gate) -> git-write either
# the promoted RFC (ai-org/rfc/<id>: rfc.json) or a send-back/reject marker.
#
# STATUS (be honest): the code BELOW is still only the manual loader/translator — the GATE (codex
# judgment + send-back, git read/write) is NOT built yet. TODO: build the gate in this form.
"""RFC receive — step 1 of the RFC phase: take in the RFC (the apex).

A raw requirement arriving at the AI Org is NOT in Linux-RFC form. The AI Org must first TRANSLATE it
into an implementable RFC (problem + proposed change + interface sketch). That translation is assumed
done here; for now the RFC is received MANUALLY (hand-written). An RFC mirrors a Linux mailing-list RFC.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass
class RFC:
    title: str
    problem: str               # what is wrong / what is needed, stated concretely (the goal)
    proposed_change: str       # the intended change / approach
    interface_sketch: str = "" # the API/contract this would introduce or touch
    notes: str = ""


def receive(source: str | Path | Mapping[str, Any]) -> RFC:
    """Load a hand-written RFC from a dict or a JSON file path."""
    if isinstance(source, Mapping):
        data = source
    elif isinstance(source, (str, Path)):
        path = Path(source)
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"Could not read RFC JSON file {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"RFC JSON file {path} is invalid JSON: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ValueError(f"RFC JSON file {path} must contain a JSON object.")
        data = loaded
    else:
        raise TypeError("receive(source) expects a dict or a path to a JSON file.")

    missing = [field for field in ("title", "problem", "proposed_change") if data.get(field) is None]
    if missing:
        raise ValueError(f"RFC is missing required field(s): {', '.join(missing)}")

    return RFC(
        title=_string_field(data, "title"),
        problem=_string_field(data, "problem"),
        proposed_change=_string_field(data, "proposed_change"),
        interface_sketch=_string_field(data, "interface_sketch", default=""),
        notes=_string_field(data, "notes", default=""),
    )


def _string_field(data: Mapping[str, Any], field: str, *, default: str | None = None) -> str:
    value = data.get(field, default)
    if not isinstance(value, str):
        raise ValueError(f"RFC field {field!r} must be a string.")
    return value
