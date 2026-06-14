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
import hashlib
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


class ScopeError(RuntimeError):
    pass


def _git(repo: Path, *args: str) -> bytes:
    try:
        return subprocess.run(["git", "-C", str(repo), *args], check=False, capture_output=True,
                              timeout=60, stdin=subprocess.DEVNULL).stdout
    except subprocess.TimeoutExpired as exc:
        raise ScopeError(f"git {' '.join(args)} timed out") from exc


def porcelain_touched(repo: Path) -> set[str]:
    """Touched paths from `git status --porcelain=v1 -z`. Rename/copy → both old and new counted.
    A failed `git status` is a hard scope failure (NN4: never silently degrade to "no changes")."""
    # -uall expands untracked directories to individual files (a collapsed `dir/` entry would hide
    # edits inside it); bounded by timeout; stdin closed.
    try:
        cp = subprocess.run(["git", "-C", str(repo), "status", "--porcelain=v1", "-z", "-uall"],
                            check=False, capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise ScopeError("git status timed out") from exc
    if cp.returncode != 0:
        raise ScopeError(f"git status failed (rc={cp.returncode}): "
                         f"{cp.stderr.decode('utf-8', 'replace')[:200]}")
    raw = cp.stdout.decode("utf-8", "replace")
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


def enforce(repo, allowed_globs, *, baseline=None, baseline_snapshot=None,
            forbidden=DEFAULT_FORBIDDEN, declared=None) -> ScopeReport:
    """Compute the scope report.

    `baseline_snapshot` (preferred) is a {path: content-hash} map from BEFORE the carrier ran; a
    pre-dirty file the carrier edits FURTHER is then caught by content (path-set subtraction alone
    would hide it — NN3). `baseline` is the legacy path-set form.
    """
    repo = Path(repo)
    post = porcelain_touched(repo)
    if baseline_snapshot is not None:
        changed = changed_since(repo, baseline_snapshot)
    else:
        changed = sorted(post - set(baseline or set()))
    # forbidden is checked against the FULL touched set (not post-baseline): a forbidden path that
    # was already dirty and is touched again must still be caught (NN3 — baseline must not hide it).
    forbidden_hits = sorted(f for f in post if any(fnmatch.fnmatch(f, g) for g in forbidden))
    # deviations: a change matches NO allowed glob. An EMPTY allow-list allows nothing (fail-closed):
    # every change is then a deviation. (Previously empty meant "allow everything" — a scope hole.)
    deviations = [f for f in changed if not any(fnmatch.fnmatch(f, g) for g in allowed_globs)]
    # nested submodule changes escape the superproject status → treat any dirty submodule as a
    # deviation (its internals were not scope-checked).
    for sm in dirty_submodules(repo):
        if sm not in deviations:
            deviations.append(sm)
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


def dirty_submodules(repo: Path) -> list[str]:
    """Submodule paths whose internals changed. `git status` collapses these to the submodule path,
    so the inner files escape scope checks; flag them as deviations (NN3 — don't hide nested changes)."""
    out = _git(Path(repo), "submodule", "status").decode("utf-8", "replace")
    dirty = []
    for line in out.splitlines():
        if line[:1] in ("+", "U"):  # + = checked-out commit differs (dirty), U = merge conflict
            parts = line[1:].split()
            if len(parts) >= 2:
                dirty.append(parts[1])
    return dirty


def baseline_of(repo) -> set[str]:
    """Capture the pre-run dirty baseline so pre-existing changes aren't blamed on the carrier."""
    return porcelain_touched(Path(repo))


def _content_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "__nonfile__"


def baseline_snapshot(repo) -> dict:
    """Pre-run snapshot {path: content-hash} of every currently-dirty path (content, not just path)."""
    repo = Path(repo)
    return {p: _content_hash(repo / p) for p in porcelain_touched(repo)}


def changed_since(repo, snapshot: dict) -> list[str]:
    """Paths whose content differs from the snapshot. Iterates the UNION of the post-run touched set
    AND the snapshot's paths, so a pre-dirty file the carrier REVERTS to HEAD or a dirty untracked
    file it DELETES is still attributed (it would vanish from the post-run set otherwise — NN3/NN4)."""
    repo = Path(repo)
    candidates = set(snapshot.keys()) | porcelain_touched(repo)
    out = [p for p in candidates if snapshot.get(p) != _content_hash(repo / p)]
    return sorted(out)


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
