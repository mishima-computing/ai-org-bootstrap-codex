#!/usr/bin/env python3
"""Finding → regression conversion — ADR-0009 #4. A persistent corpus of gate counterexamples, replayed
deterministically on every future run so a fixed bug that reappears is caught instantly by a cheap
deterministic check — not re-discovered by an LLM review/repair, which re-pays cost the org already spent
(the structural answer to "40% of steps are repair").

Most gates already self-regress: conformance re-checks the contract's declared examples, secret-scan
re-scans, pre-flight re-validates the contract — every leaf, deterministically. The exception is FUZZING,
whose inputs are generated stochastically, so a previously-found crash input might not be regenerated. The
corpus closes that gap: a fuzz counterexample is stored and replayed first on every later run, turning a
one-time discovery into a permanent regression test.

Storage is an append-only JSONL keyed by gate, deduped on load, and bounded so it cannot grow without limit.
Fail-soft throughout: a corpus error never breaks a run (a verification aid must not become a liability).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_MAX_ENTRIES = 500           # per gate: a regression corpus is a focused set of known-bad inputs, not a lake


def _key(entry: dict) -> tuple:
    """Dedupe identity of a corpus entry — the input that reproduces the bug, not the (volatile) detail."""
    return (entry.get("gate"), entry.get("kind"), entry.get("arg"), entry.get("stdin"))


def default_path(repo) -> str:
    """Where the corpus lives. REGRESSION_CORPUS overrides; else it sits beside the run's stream so it
    persists with the repo's .agent-runs, not in an ephemeral leaf worktree."""
    env = os.environ.get("REGRESSION_CORPUS")
    return env if env else str(Path(repo) / ".agent-runs" / "regressions.jsonl")


def load(path: str | None, gate: str | None = None) -> list[dict]:
    if not path or not Path(path).is_file():
        return []
    out, seen = [], set()
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(e, dict) or (gate is not None and e.get("gate") != gate):
                continue
            k = _key(e)
            if k in seen:
                continue
            seen.add(k)
            out.append(e)
    except OSError:
        return out
    return out


def record(path: str | None, entries: list[dict]) -> int:
    """Append entries not already present (deduped against the whole corpus). Returns the number newly added.
    Bounded per gate at _MAX_ENTRIES (oldest kept; new ones past the cap are dropped — and that drop is the
    caller's to surface, never silent in aggregate)."""
    if not path or not entries:
        return 0
    try:
        existing = load(path, gate=None)
        seen = {_key(e) for e in existing}
        per_gate: dict = {}
        for e in existing:
            per_gate.setdefault(e.get("gate"), 0)
            per_gate[e.get("gate")] += 1
        fresh = []
        for e in entries:
            k = _key(e)
            if k in seen:
                continue
            g = e.get("gate")
            if per_gate.get(g, 0) >= _MAX_ENTRIES:
                continue
            seen.add(k)
            per_gate[g] = per_gate.get(g, 0) + 1
            fresh.append(e)
        if not fresh:
            return 0
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            for e in fresh:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        return len(fresh)
    except OSError:
        return 0
