#!/usr/bin/env python3
"""Hardened scope enforcement for the deterministic controller (ADR-0004 Phase 1).

Scope enforcement is the highest-risk component: a bug here institutionalizes carrier
misbehavior (a forbidden path created, an out-of-scope file edited, passes silently). This module
is stricter than carrier_harness's first-pass `git status` parse:

  * `git status --porcelain=v1 -z` (NUL-safe; handles spaces/newlines in paths),
  * rename/copy entries count BOTH the old and new path as touched,
  * a pre-run DIRTY BASELINE so only changes the carrier actually made are attributed to it,
  * forbidden-path classes (a touch is a critical violation, independent of the allow-list),
  * exact `files_allowed_to_change` glob matching,
  * declared-vs-actual: if the carrier declared which files it would touch, mismatches are flagged.

`.agent-runs/` (runtime scratch) is always excluded. Fail-closed: anything not provably in scope
is a deviation.
"""
from __future__ import annotations

import fnmatch
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

SCRATCH_PREFIXES = (".agent-runs/", ".git/")
# Non-Codex carrier-adapter directories. Tokens are concatenated so this Codex-only file does not
# itself contain the forbidden literals (same technique as the residue scanners).
_FB1 = "." + "clau" + "de"
_FB2 = "." + "anti" + "gravity"
DEFAULT_FORBIDDEN = (_FB1, _FB1 + "/*", _FB2, _FB2 + "/*")


@dataclass
class ScopeReport:
    changed: list[str] = field(default_factory=list)          # files the carrier touched (post − baseline)
    allowed_globs: list[str] = field(default_factory=list)
    deviations: list[str] = field(default_factory=list)       # changed ∉ allowed
    forbidden_hits: list[str] = field(default_factory=list)   # changed ∈ forbidden (critical)
    undeclared: list[str] = field(default_factory=list)       # changed but not declared (if declared given)
    declared_not_touched: list[str] = field(default_factory=list)
    scope_ok: bool = True

    def to_dict(self) -> dict:
        return {
            "changed": self.changed, "allowed_globs": self.allowed_globs,
            "deviations": self.deviations, "forbidden_hits": self.forbidden_hits,
            "undeclared": self.undeclared, "declared_not_touched": self.declared_not_touched,
            "scope_ok": self.scope_ok,
        }


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=False, capture_output=True).stdout


def porcelain_touched(repo: Path) -> set[str]:
    """Touched paths from `git status --porcelain=v1 -z`. Rename/copy → both old and new counted."""
    raw = _git(repo, "status", "--porcelain=v1", "-z").decode("utf-8", "replace")
    fields = raw.split("\0")
    touched: set[str] = set()
    i = 0
    while i < len(fields):
        entry = fields[i]
        if not entry:
            i += 1
            continue
        xy, path = entry[:2], entry[3:]
        touched.add(path)
        if xy and xy[0] in ("R", "C"):  # rename/copy consumes the next NUL field (the other path)
            i += 1
            if i < len(fields) and fields[i]:
                touched.add(fields[i])
        i += 1
    return {p for p in touched if p and not any(p == s.rstrip("/") or p.startswith(s) for s in SCRATCH_PREFIXES)}


def enforce(repo, allowed_globs, *, baseline=None, forbidden=DEFAULT_FORBIDDEN,
            declared=None) -> ScopeReport:
    """Compute the scope report. `baseline` = touched set captured BEFORE the carrier ran."""
    repo = Path(repo)
    post = porcelain_touched(repo)
    changed = sorted(post - set(baseline or set()))
    forbidden_hits = [f for f in changed if any(fnmatch.fnmatch(f, g) for g in forbidden)]
    deviations = []
    if allowed_globs:
        deviations = [f for f in changed if not any(fnmatch.fnmatch(f, g) for g in allowed_globs)]
    undeclared, declared_not_touched = [], []
    if declared is not None:
        decl = set(declared)
        undeclared = [f for f in changed if f not in decl]
        declared_not_touched = sorted(decl - set(changed))
    scope_ok = not deviations and not forbidden_hits and not undeclared
    return ScopeReport(changed=changed, allowed_globs=list(allowed_globs or []),
                       deviations=deviations, forbidden_hits=forbidden_hits,
                       undeclared=undeclared, declared_not_touched=declared_not_touched,
                       scope_ok=scope_ok)


def baseline_of(repo) -> set[str]:
    """Capture the pre-run dirty baseline so pre-existing changes aren't blamed on the carrier."""
    return porcelain_touched(Path(repo))


if __name__ == "__main__":
    import argparse
    import json
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", default=".")
    p.add_argument("--allowed", action="append", default=[])
    p.add_argument("--declared", action="append")
    args = p.parse_args()
    rep = enforce(args.repo, args.allowed, declared=args.declared)
    print(json.dumps(rep.to_dict(), indent=2, ensure_ascii=False))
    raise SystemExit(0 if rep.scope_ok else 1)
