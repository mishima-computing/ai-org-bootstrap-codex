#!/usr/bin/env python3
"""Lightweight TQ-MCP client for the carrier harness.

Queries a TQ-MCP server (which may live on another machine) to retrieve
time-space episodic context, then formats it for prompt injection.

The harness calls this BEFORE launching a carrier so the carrier receives
grounded context without needing network access itself.

Zero external dependencies — uses only urllib from stdlib.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class TQClient:
    """Minimal HTTP client for TQ-MCP REST API."""

    def __init__(self, base_url: str = "http://127.0.0.1:8000", timeout: int = 5):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, body: dict) -> dict | None:
        """POST JSON and return parsed response, or None on failure."""
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            return None

    def _get(self, path: str) -> dict | None:
        url = f"{self.base_url}{path}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            return None

    def is_available(self) -> bool:
        """Check if the TQ-MCP server is reachable."""
        health = self._get("/health")
        return health is not None and health.get("status") == "ok"

    def query(self, text: str, *, top_k: int = 5,
              time_layer: str | None = None,
              space_layer: str | None = None) -> list[dict]:
        """Semantic search over episodic memories.

        Returns list of {"time_layer", "space_layer", "content", "score"}.
        """
        body: dict = {"query": text, "top_k": top_k}
        if time_layer:
            body["time_layer"] = time_layer
        if space_layer:
            body["space_layer"] = space_layer

        resp = self._post("/query", body)
        if not resp:
            return []

        results = []
        for r in resp.get("results", []):
            ep = r.get("episode", {})
            results.append({
                "time_layer": ep.get("time_layer", ""),
                "space_layer": ep.get("space_layer", ""),
                "content": ep.get("content", ""),
                "score": r.get("score", 0.0),
            })
        return results

    def add_episode(self, content: str, time_layer: str = "T2",
                    space_layer: str = "Q3", metadata: dict | None = None) -> str | None:
        """Add an episode and return its ID, or None on failure."""
        body = {
            "time_layer": time_layer,
            "space_layer": space_layer,
            "content": content,
        }
        if metadata:
            body["metadata"] = metadata
        resp = self._post("/episodes", body)
        return resp.get("id") if resp else None


# ---------------------------------------------------------------------------
# Context formatting for prompt injection
# ---------------------------------------------------------------------------

_TQ_LABELS = {
    "T1": "present", "T2": "recent", "T3": "long-term",
    "Q1": "local", "Q2": "structured", "Q3": "abstract",
}


def format_context(results: list[dict], query: str) -> str:
    """Format TQ query results into a text block for prompt prepending.

    Example output:
        --- TQ Context (time-space episodic memory) ---
        Query: implement the new endpoint
        [T1/Q3 present/abstract] (0.82) I am in deep work mode, focusing on TQ-MCP.
        [T2/Q1 recent/local]    (0.71) 30 minutes ago I fixed the embedding bug.
        ---
    """
    if not results:
        return ""

    lines = [
        "--- TQ Context (time-space episodic memory) ---",
        f"Query: {query}",
    ]
    for r in results:
        t = r["time_layer"]
        q = r["space_layer"]
        t_label = _TQ_LABELS.get(t, t)
        q_label = _TQ_LABELS.get(q, q)
        score = r["score"]
        content = r["content"]
        lines.append(f"[{t}/{q} {t_label}/{q_label}] ({score:.2f}) {content}")
    lines.append("---")
    return "\n".join(lines)


def retrieve_context(prompt: str, base_url: str = "http://127.0.0.1:8000",
                     top_k: int = 5) -> str:
    """One-shot: query TQ-MCP and return formatted context, or "" if unavailable.

    This is the main entry point for harness integration:
        context = retrieve_context(prompt)
        full_prompt = context + "\\n\\n" + prompt  # if context is non-empty
    """
    client = TQClient(base_url=base_url)
    if not client.is_available():
        return ""
    results = client.query(prompt, top_k=top_k)
    return format_context(results, prompt)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def self_test() -> int:
    """Verify client logic (does NOT require a running server)."""
    fails: list[str] = []

    # 1. format_context with synthetic data
    results = [
        {"time_layer": "T1", "space_layer": "Q1", "content": "at desk", "score": 0.9},
        {"time_layer": "T3", "space_layer": "Q3", "content": "long-term goal", "score": 0.5},
    ]
    formatted = format_context(results, "test query")
    if "TQ Context" not in formatted:
        fails.append("format_context must contain header")
    if "[T1/Q1 present/local]" not in formatted:
        fails.append("format_context must contain TQ labels")
    if "(0.90)" not in formatted:
        fails.append("format_context must contain score")
    if "test query" not in formatted:
        fails.append("format_context must contain query")

    # 2. format_context with empty results
    empty = format_context([], "nothing")
    if empty != "":
        fails.append("empty results should produce empty string")

    # 3. TQClient with unreachable server should not crash
    client = TQClient(base_url="http://127.0.0.1:59999", timeout=1)
    if client.is_available():
        fails.append("unreachable server should return False")
    if client.query("test"):
        fails.append("unreachable server query should return []")

    # 4. retrieve_context with unreachable server
    ctx = retrieve_context("test", base_url="http://127.0.0.1:59999")
    if ctx != "":
        fails.append("retrieve_context should return '' when server is down")

    if fails:
        for f in fails:
            print("FAIL " + f)
        return 1
    print("tq_client self-test passed "
          "(format_context, graceful degrade, unreachable server).")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(self_test())
