#!/usr/bin/env python3
"""Append-only, content-addressed run journal for the deterministic controller (ADR-0004 Phase 1).

Every phase records an immutable event keyed by content hashes (prompt, discipline, diff, contract)
under `.agent-runs/controller/<run-id>/journal.jsonl`. This gives replay/audit behavior (an event
history): later edits to working files cannot rewrite what a phase actually consumed, because the
journal records hashes, not mutable references. Retry safety: each attempt is a separate event.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str | None:
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


class RunJournal:
    """Append-only journal under .agent-runs/controller/<run_id>/journal.jsonl."""

    GENESIS = "genesis"

    def __init__(self, repo, run_id: str, *, clock=None):
        self.repo = Path(repo)
        self.run_id = run_id
        self.dir = self.repo / ".agent-runs" / "controller" / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "journal.jsonl"
        # clock injected for determinism/testability (no argless time in restartable contexts)
        self._clock = clock or (lambda: int(time.time()))
        existing = self._read_raw()
        self._seq = len(existing)
        self._last_hash = existing[-1]["event_hash"] if existing else self.GENESIS

    @staticmethod
    def _hash_event(event_without_hash: dict) -> str:
        return hashlib.sha256(
            json.dumps(event_without_hash, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()

    def _read_raw(self) -> list[dict]:
        if not self.path.is_file():
            return []
        return [json.loads(line) for line in self.path.open("r", encoding="utf-8") if line.strip()]

    def append(self, phase: str, payload: dict) -> dict:
        event = {"seq": self._seq, "ts": self._clock(), "run_id": self.run_id,
                 "phase": phase, "prev_hash": self._last_hash, **payload}
        event["event_hash"] = self._hash_event(event)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._seq += 1
        self._last_hash = event["event_hash"]
        return event

    def events(self, verify: bool = True) -> list[dict]:
        """Read the journal; with verify=True (default) recompute the hash chain and raise on tamper."""
        raw = self._read_raw()
        if verify:
            prev = self.GENESIS
            for e in raw:
                stored = e.get("event_hash")
                recomputed = self._hash_event({k: v for k, v in e.items() if k != "event_hash"})
                if stored != recomputed:
                    raise ValueError(f"journal tamper: event seq={e.get('seq')} hash mismatch")
                if e.get("prev_hash") != prev:
                    raise ValueError(f"journal tamper: event seq={e.get('seq')} broken chain")
                prev = stored
        return raw


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        j = RunJournal(d, "run-1", clock=lambda: 1000)
        j.append("contract", {"contract_sha256": sha256_text("hello")})
        j.append("carrier", {"attempt": 0, "exit": 0})
        evs = RunJournal(d, "run-1", clock=lambda: 1000).events()
        assert [e["seq"] for e in evs] == [0, 1], evs
        assert evs[0]["phase"] == "contract"
        print("controller_evidence smoke ok")
