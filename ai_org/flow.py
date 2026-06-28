"""Compatibility module for the Git-driven driver.

The old monolithic ``run()`` entrypoint was removed. Use ``advance()`` to fire
one Git-state-derived step, ``status()`` to inspect progress, or ``main()`` to
loop until terminal.
"""
from __future__ import annotations

from .driver import advance, main, status

__all__ = ["advance", "main", "status"]
