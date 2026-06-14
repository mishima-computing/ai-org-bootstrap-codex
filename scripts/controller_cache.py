#!/usr/bin/env python3
"""Content-addressed carrier cache / replay (the biggest token-reduction lever).

A carrier run is the dominant token cost. If the SAME contract is run from the SAME repo state and
already produced a successful result, re-running the carrier spends the whole carrier token budget
again for nothing. This stores the carrier's CHANGE BUNDLE keyed by sha256(contract + state); on a
hit it REPLAYS the bundle (applies the stored changes) instead of launching the carrier — skipping
the LLM call entirely.

Safety (conservative — a stale/bad cache must never corrupt, only fall back to a real run):
  * key binds the canonical contract AND the pre-run state (HEAD + dirty-baseline snapshot); a hit
    requires the recorded state_hash to equal the current one,
  * replay applies the bundle then VERIFIES the resulting change set equals the recorded one; any
    mismatch (or an unclean `git apply`) is a cache MISS → the carrier runs,
  * the deterministic gates (scope / quality / verifiers) are always re-run on the replayed tree —
    they are zero-token, so correctness is re-confirmed, never trusted from cache.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import controller_scope as scope  # noqa: E402


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _git(repo: Path, *args, input_bytes=None):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          input=input_bytes, timeout=60, stdin=None if input_bytes else subprocess.DEVNULL)


def state_hash(repo: Path, snapshot: dict) -> str:
    head = _git(repo, "rev-parse", "HEAD").stdout.decode("utf-8", "replace").strip()
    return _sha(head + "\0" + json.dumps(snapshot, sort_keys=True))


def contract_key(contract_dict: dict, state: str) -> str:
    return _sha(json.dumps(contract_dict, sort_keys=True, ensure_ascii=False) + "\0" + state)


def _cache_dir(repo: Path) -> Path:
    d = Path(repo) / ".agent-runs" / "controller" / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_rel(repo: Path, rel: str) -> bool:
    """A bundle path must be repo-relative with no `..`/absolute/symlink escape (NN3 — a poisoned
    bundle must not write outside the repo)."""
    if not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
        return False
    try:
        (repo.resolve() / rel).resolve().relative_to(repo.resolve())
        return True
    except ValueError:
        return False


def _manifest(bundle: dict) -> str:
    payload = {k: bundle[k] for k in ("key", "state_hash", "changed", "tracked_diff", "files",
                                      "content_hashes", "carrier_result") if k in bundle}
    return _sha(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def store(repo, key: str, state: str, snapshot: dict, carrier_result: dict) -> dict:
    """Capture the carrier's change bundle (tracked diff + untracked contents + per-path content
    hashes + a manifest digest) under the key."""
    repo = Path(repo)
    changed = scope.changed_since(repo, snapshot)
    tracked_diff = _git(repo, "diff").stdout.decode("utf-8", "replace")
    untracked, content_hashes = {}, {}
    for p in changed:
        fp = repo / p
        content_hashes[p] = scope._content_hash(fp)  # final-state hash for replay verification
        if fp.is_file():
            untracked[p] = fp.read_text(encoding="utf-8", errors="replace")
    bundle = {"key": key, "state_hash": state, "changed": changed, "tracked_diff": tracked_diff,
              "files": untracked, "content_hashes": content_hashes,
              "carrier_result": {"ok": carrier_result.get("ok"), "attempts": carrier_result.get("attempts", [])}}
    bundle["manifest"] = _manifest(bundle)
    (_cache_dir(repo) / f"{key}.json").write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    return bundle


def lookup(repo, key: str, state: str) -> dict | None:
    p = _cache_dir(Path(repo)) / f"{key}.json"
    if not p.is_file():
        return None
    try:
        bundle = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    # integrity: key + state must match, and the manifest must recompute (reject a tampered bundle)
    if bundle.get("key") != key or bundle.get("state_hash") != state:
        return None
    if bundle.get("manifest") != _manifest(bundle):
        return None
    return bundle


def replay(repo, bundle: dict, snapshot: dict) -> bool:
    """Transactionally apply the bundle; verify changed-set AND per-path content; rollback on any
    mismatch (a failed replay must leave NO residue, else the carrier would run on a contaminated
    tree). Returns True only on a fully-verified replay."""
    repo = Path(repo)
    files = bundle.get("files", {})
    changed = bundle.get("changed", [])
    content_hashes = bundle.get("content_hashes", {})
    diff = bundle.get("tracked_diff", "")

    # preflight (NO mutation): every path safe, and the tracked diff applies cleanly
    for rel in set(files) | set(changed):
        if not _safe_rel(repo, rel):
            return False
    if diff.strip() and _git(repo, "apply", "--check", "--whitespace=nowarn",
                             input_bytes=diff.encode("utf-8")).returncode != 0:
        return False

    written = []
    try:
        for rel, content in files.items():
            fp = repo / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8")
            written.append(rel)
        if diff.strip() and _git(repo, "apply", "--whitespace=nowarn",
                                 input_bytes=diff.encode("utf-8")).returncode != 0:
            raise RuntimeError("apply failed after --check passed")
        # verify: change set AND per-path content hashes both match the recorded bundle
        if scope.changed_since(repo, snapshot) != changed:
            raise RuntimeError("replayed change set differs")
        for p in changed:
            if scope._content_hash(repo / p) != content_hashes.get(p):
                raise RuntimeError(f"replayed content differs for {p}")
        return True
    except Exception:  # noqa: BLE001 — any failure → roll back, report cache miss
        for rel in written:
            (repo / rel).unlink(missing_ok=True)
        tracked = [p for p in changed if p not in files]
        if tracked:
            _git(repo, "checkout", "--", *tracked)
        return False


if __name__ == "__main__":
    print("controller_cache is a library used by controller_workflow when cache_enabled=True.")
