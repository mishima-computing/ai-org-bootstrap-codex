"""Shared totality helpers for the deterministic structure gadgets package.

Leaf module on purpose: both the package __init__ (skeleton/repair gadgets) and
registry.py (hint matching) import from here, keeping the intra-package import
graph acyclic. The contract these helpers enforce is package-wide: every public
gadget is pure and total — malformed input yields a typed error dict, and
exceptions never escape.
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable


def _gadget_error(error_type: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"type": error_type, "message": message}}


def _total(func: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Totality guarantee: a gadget returns a typed error, it never raises.

    Explicit input validation inside each gadget is the primary defense; this
    wrapper only enforces the stated contract for inputs no validation foresaw.
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - the contract is "never escapes"
            return _gadget_error("internal_error", f"{type(exc).__name__}: {exc}")

    return wrapper
