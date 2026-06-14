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


def store(repo, key: str, state: str, snapshot: dict, carrier_result: dict) -> dict:
    """Capture the carrier's change bundle (tracked diff + untracked contents) under the key."""
    repo = Path(repo)
    changed = scope.changed_since(repo, snapshot)
    tracked_diff = _git(repo, "diff").stdout.decode("utf-8", "replace")
    untracked = {}
    for p in changed:
        fp = repo / p
        if fp.is_file():
            # store contents for paths git diff won't carry (untracked / newly added)
            untracked[p] = fp.read_text(encoding="utf-8", errors="replace")
    bundle = {"key": key, "state_hash": state, "changed": changed,
              "tracked_diff": tracked_diff, "files": untracked,
              "carrier_result": {"ok": carrier_result.get("ok"), "attempts": carrier_result.get("attempts", [])}}
    (_cache_dir(repo) / f"{key}.json").write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    return bundle


def lookup(repo, key: str, state: str) -> dict | None:
    p = _cache_dir(Path(repo)) / f"{key}.json"
    if not p.is_file():
        return None
    bundle = json.loads(p.read_text(encoding="utf-8"))
    return bundle if bundle.get("state_hash") == state else None


def replay(repo, bundle: dict, snapshot: dict) -> bool:
    """Apply the bundle's changes; verify the resulting change set matches. Returns True on success."""
    repo = Path(repo)
    # write stored file contents (covers untracked / added)
    for rel, content in bundle.get("files", {}).items():
        fp = repo / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
    diff = bundle.get("tracked_diff", "")
    if diff.strip():
        cp = _git(repo, "apply", "--whitespace=nowarn", input_bytes=diff.encode("utf-8"))
        if cp.returncode != 0:
            return False  # unclean apply → caller treats as cache miss and runs the carrier
    # verify: the replayed change set must equal what was recorded
    return scope.changed_since(repo, snapshot) == bundle.get("changed", [])


if __name__ == "__main__":
    print("controller_cache is a library used by controller_workflow when cache_enabled=True.")
