"""Locating a target inside a committed JSON body.

Maps a raw-text offset (where a --term matched) to a JSON path, then to the
nearest enclosing node that carries an "id" field -- AI Org plan bodies tag
their semantic nodes with ids like "constraint:hard:28", and the committed
review-round delta records name exactly those ids. That is the hinge between
"a spot in the text" and "a versioned node the org's records talk about".

Pure stdlib, position-tracking recursive-descent scanner. Deterministic:
document order everywhere.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from json.decoder import scanstring
from typing import Any, Optional

_WS = " \t\n\r"
_NUMBER_RE = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][-+]?\d+)?")


class BodyParseError(ValueError):
    pass


@dataclass
class ScalarLoc:
    """A scalar value with its raw-text span and JSON path."""

    path: tuple
    start: int  # offset of the first char (opening quote for strings)
    end: int  # offset one past the last char (closing quote for strings)
    value: Any


def index_scalars(text: str) -> list[ScalarLoc]:
    """All scalar values of a JSON document, in document order."""
    out: list[ScalarLoc] = []
    i = _skip_ws(text, 0)
    i = _parse_value(text, i, (), out)
    return out


def _skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i] in _WS:
        i += 1
    return i


def _parse_value(s: str, i: int, path: tuple, out: list) -> int:
    if i >= len(s):
        raise BodyParseError(f"unexpected end of document at {i}")
    c = s[i]
    if c == '"':
        value, end = scanstring(s, i + 1)
        out.append(ScalarLoc(path, i, end, value))
        return end
    if c == "{":
        return _parse_object(s, i, path, out)
    if c == "[":
        return _parse_array(s, i, path, out)
    for literal, pyval in (("true", True), ("false", False), ("null", None)):
        if s.startswith(literal, i):
            out.append(ScalarLoc(path, i, i + len(literal), pyval))
            return i + len(literal)
    m = _NUMBER_RE.match(s, i)
    if m:
        raw = m.group(0)
        value = json.loads(raw)
        out.append(ScalarLoc(path, i, m.end(), value))
        return m.end()
    raise BodyParseError(f"unexpected character {c!r} at offset {i}")


def _parse_object(s: str, i: int, path: tuple, out: list) -> int:
    i = _skip_ws(s, i + 1)
    if i < len(s) and s[i] == "}":
        return i + 1
    while True:
        if i >= len(s) or s[i] != '"':
            raise BodyParseError(f"expected object key at offset {i}")
        key, i = scanstring(s, i + 1)
        i = _skip_ws(s, i)
        if i >= len(s) or s[i] != ":":
            raise BodyParseError(f"expected ':' at offset {i}")
        i = _skip_ws(s, i + 1)
        i = _parse_value(s, i, path + (key,), out)
        i = _skip_ws(s, i)
        if i < len(s) and s[i] == ",":
            i = _skip_ws(s, i + 1)
            continue
        if i < len(s) and s[i] == "}":
            return i + 1
        raise BodyParseError(f"expected ',' or '}}' at offset {i}")


def _parse_array(s: str, i: int, path: tuple, out: list) -> int:
    i = _skip_ws(s, i + 1)
    if i < len(s) and s[i] == "]":
        return i + 1
    idx = 0
    while True:
        i = _parse_value(s, i, path + (idx,), out)
        i = _skip_ws(s, i)
        if i < len(s) and s[i] == ",":
            i = _skip_ws(s, i + 1)
            idx += 1
            continue
        if i < len(s) and s[i] == "]":
            return i + 1
        raise BodyParseError(f"expected ',' or ']' at offset {i}")


# ---------------------------------------------------------------------------
# Navigation helpers
# ---------------------------------------------------------------------------

def navigate(data: Any, path: tuple) -> Any:
    """Follow a path of keys/indexes; raises KeyError/IndexError on miss."""
    node = data
    for seg in path:
        node = node[seg]
    return node


def parse_node_arg(arg: str) -> tuple:
    """Parse a CLI json-path like '/problem/constraints/0' into a path tuple."""
    parts = [p for p in arg.split("/") if p != ""]
    path: list = []
    for p in parts:
        path.append(int(p) if re.fullmatch(r"\d+", p) else p)
    return tuple(path)


def enclosing_id_node(data: Any, scalar_path: tuple) -> tuple:
    """(node_id, node_path) of the deepest enclosing dict carrying an 'id'.

    Returns (None, None) when no ancestor carries a string 'id'.
    """
    for cut in range(len(scalar_path), -1, -1):
        prefix = scalar_path[:cut]
        try:
            node = navigate(data, prefix)
        except (KeyError, IndexError, TypeError):
            continue
        if isinstance(node, dict):
            node_id = node.get("id")
            if isinstance(node_id, str) and node_id:
                return node_id, prefix
    return None, None


def find_node_by_id(data: Any, node_id: str) -> Optional[Any]:
    """First node (document order) whose 'id' equals node_id."""
    stack = [data]
    # Depth-first in insertion order keeps the search deterministic.
    while stack:
        node = stack.pop(0)
        if isinstance(node, dict):
            if node.get("id") == node_id:
                return node
            stack = list(node.values()) + stack
        elif isinstance(node, list):
            stack = list(node) + stack
    return None


def canonical(node: Any) -> str:
    """Order-insensitive canonical form used for change detection."""
    return json.dumps(node, sort_keys=True, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------

def locate_term(text: str, term: str) -> Optional[ScalarLoc]:
    """First scalar containing `term` (raw offset first, decoded fallback)."""
    scalars = index_scalars(text)
    pos = text.find(term)
    if pos >= 0:
        for sc in scalars:
            if sc.start <= pos < sc.end:
                return sc
    # The term may be escaped in the raw text (e.g. non-ASCII); fall back to
    # decoded values in document order.
    for sc in scalars:
        if isinstance(sc.value, str) and term in sc.value:
            return sc
    return None

