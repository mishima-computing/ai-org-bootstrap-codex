#!/usr/bin/env python3
"""Adapter: use Codex's native `codex review` as a reviewer, in the shape the pipeline expects.

`codex review` is diff-anchored (it starts from the change set) but NOT diff-limited — it reads the
full changed files, their HEAD base, and unchanged dependents to judge cross-file impact (verified:
flagged a break in an *unchanged* caller). Its findings stay on the deliverable, not on scratch the way
a free-reading role carrier did. So it is a stronger fit for the review role than a generic carrier.

Output is a transcript followed by a final review block of priority-tagged findings:

    - [P1] Preserve the timeout parameter — /repo/lib.py:1-1
      When `nums` is empty, `len(nums)` is 0, so ...

This module parses that into `{"findings": [{file, line_range, severity, priority, claim, title}], "ok"}`
so `_linon_findings` (and the `while findings` repair loop) consume it unchanged. Parsing is pure and
unit-tested; the subprocess wrapper is a thin shell.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

# `- [P1] <title> — <file>:<start>[-<end>]`  (em-dash separator; file may be absolute; end optional)
_FINDING_RE = re.compile(
    r"^[-*]\s*\[P(?P<prio>\d+)\]\s+(?P<title>.*?)\s+[—-]\s+(?P<file>.+?):(?P<start>\d+)(?:-(?P<end>\d+))?\s*$"
)
# P1 = blocking/critical, P2 = major, P3+ = minor. The repair loop treats ANY finding as blocking, so
# severity is advisory here; priority is preserved for downstream filtering/ranking.
_PRIORITY_SEVERITY = {1: "critical", 2: "major", 3: "minor"}


def parse_review_output(text: str, repo: str | None = None) -> list[dict]:
    """Parse `codex review` output into findings. A finding's body is the indented lines that follow its
    header until the next header / dedent. File paths are made repo-relative when `repo` is given (so the
    verdict-scope filter and provenance see the same shape as the role-carrier reviewer produced)."""
    findings: list[dict] = []
    lines = (text or "").splitlines()
    i = 0
    while i < len(lines):
        m = _FINDING_RE.match(lines[i].rstrip())
        if not m:
            i += 1
            continue
        start = int(m.group("start"))
        end = int(m.group("end")) if m.group("end") else start
        file = m.group("file").strip()
        if repo:
            try:
                file = str(Path(file).resolve().relative_to(Path(repo).resolve()))
            except (ValueError, OSError):
                pass  # already relative, or outside repo — keep as-is
        # gather the indented body block
        body: list[str] = []
        j = i + 1
        while j < len(lines) and (lines[j].strip() == "" or lines[j].startswith((" ", "\t"))):
            if lines[j].strip():
                body.append(lines[j].strip())
            elif body:                                  # blank line after body started -> block ends
                break
            j += 1
        prio = int(m.group("prio"))
        findings.append({
            "file": file,
            "line_range": {"start": start, "end": end},
            "priority": prio,
            "severity": _PRIORITY_SEVERITY.get(prio, "minor"),
            "title": m.group("title").strip(),
            "claim": " ".join(body) if body else m.group("title").strip(),
            "source": "codex-review",
        })
        i = j
    # `codex review` prints its final review block more than once; collapse exact repeats
    # (same file + span + title) so a single issue is not counted as several findings.
    deduped: list[dict] = []
    seen: set = set()
    for f in findings:
        key = (f["file"], f["line_range"]["start"], f["line_range"]["end"], f["title"])
        if key not in seen:
            seen.add(key)
            deduped.append(f)
    return deduped


def review(repo: str, *, base: str | None = None, timeout: int = 600) -> dict:
    """Run `codex review` in `repo` and return `{"findings": [...], "ok": bool, "raw": str}`.

    Diff scope: `--base <branch>` if given, else `--uncommitted` (staged+unstaged+untracked — the leaf's
    accumulated work). `ok` is False on a non-zero/timed-out review (treat as "could not review", not
    "clean"). `codex review` has no `-C`, so it runs with cwd=repo and stdin closed (the hang guard)."""
    argv = ["codex", "review"]
    argv += (["--base", base] if base else ["--uncommitted"])
    try:
        cp = subprocess.run(argv, cwd=str(repo), capture_output=True, text=True,
                            timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raw = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        return {"findings": parse_review_output(raw, repo), "ok": False, "raw": raw, "timed_out": True}
    raw = cp.stdout or ""
    return {"findings": parse_review_output(raw, repo), "ok": cp.returncode == 0, "raw": raw}


def self_test() -> int:
    sample = (
        "codex\n"
        "The changed `fetch` signature breaks an existing caller.\n\n"
        "Review comment:\n\n"
        "- [P1] Preserve the timeout parameter — /repo/lib.py:1-1\n"
        "  Existing code still calls `fetch` with two arguments, e.g. `caller.py`\n"
        "  invokes fetch(\"http://x\", 30), so this signature change makes run() fail.\n"
        "- [P2] Handle empty input in average — /repo/calc.py:6-6\n"
        "  When nums is empty, len(nums) is 0, so average([]) raises ZeroDivisionError.\n"
    )
    fs = parse_review_output(sample, repo="/repo")
    assert len(fs) == 2, fs
    assert fs[0]["file"] == "lib.py" and fs[0]["line_range"] == {"start": 1, "end": 1}, fs[0]
    assert fs[0]["priority"] == 1 and fs[0]["severity"] == "critical", fs[0]
    assert "two arguments" in fs[0]["claim"], fs[0]
    assert fs[1]["file"] == "calc.py" and fs[1]["priority"] == 2, fs[1]
    # no findings -> empty (a clean review)
    assert parse_review_output("codex\nLooks good. No issues found.\n") == []
    print("codex_review self-test passed (parse priority/file/line-range/body, repo-relative).")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
