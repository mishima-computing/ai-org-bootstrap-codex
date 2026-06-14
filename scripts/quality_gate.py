#!/usr/bin/env python3
"""Post-edit quality gate for the implementer carrier.

Deterministic code-quality checks that run AFTER the implementer edits files,
BEFORE scope enforcement.  Fixes what can be fixed mechanically, flags what
needs human or carrier attention, and optionally reverts if tests fail (TCR).

Every check is an optional tool probe: if the tool is not installed the check
is skipped and logged as 'unavailable'.  The harness never fails because a
quality tool is missing — it degrades gracefully.

This module has ZERO external Python dependencies (stdlib only).  Quality tools
(ruff, biome, ast-grep) are invoked as subprocesses — the user installs them
independently.

CLI:
  quality_gate.py check --repo R --files f1 f2 ... [--test-cmd CMD]
  quality_gate.py --self-test
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Tool probing
# ---------------------------------------------------------------------------

def probe_tool(name: str) -> str | None:
    """Return absolute path if *name* is on PATH, else None."""
    return shutil.which(name)


def _run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, check=False, **kw)


# ---------------------------------------------------------------------------
# Fast gate — deterministic auto-fixers (milliseconds, no LLM)
# ---------------------------------------------------------------------------

def fast_fix(changed_files: list[str], repo: Path) -> dict:
    """Run deterministic auto-fixers on *changed_files*.

    - Python files: ``ruff check --fix`` + ``ruff format``
    - JS/TS files:  ``biome check --fix`` (if available)

    Returns ``{"fixed_count": int, "tools_used": [], "tools_unavailable": []}``.
    """
    py = [f for f in changed_files if f.endswith(".py")]
    js = [f for f in changed_files if f.endswith((".js", ".ts", ".tsx", ".jsx"))]
    used: list[str] = []
    unavail: list[str] = []
    fixed = 0

    ruff = probe_tool("ruff")
    if py and ruff:
        abs_py = [str(repo / f) for f in py]
        r1 = _run([ruff, "check", "--fix", "--exit-zero"] + abs_py, cwd=str(repo))
        r2 = _run([ruff, "format"] + abs_py, cwd=str(repo))
        used.append("ruff")
        # count how many files ruff reports as fixed/formatted
        fixed += sum(1 for r in (r1, r2) if r.returncode == 0 and r.stderr)
    elif py:
        unavail.append("ruff")

    biome = probe_tool("biome")
    if js and biome:
        abs_js = [str(repo / f) for f in js]
        _run([biome, "check", "--fix"] + abs_js, cwd=str(repo))
        used.append("biome")
        fixed += len(js)
    elif js:
        unavail.append("biome")

    return {"fixed_count": fixed, "tools_used": used, "tools_unavailable": unavail}


# ---------------------------------------------------------------------------
# Lint check — structured error output
# ---------------------------------------------------------------------------

def lint_check(changed_files: list[str], repo: Path) -> dict:
    """Run linters and return structured errors.

    Tools tried (all optional):
    - ruff check --output-format json  (Python)
    - biome check --reporter json      (JS/TS)
    - ast-grep scan --json             (any, if .ast-grep/ rules exist)

    Returns ``{"pass": bool, "errors": [{"file","line","rule","message"}], ...}``.
    """
    errors: list[dict] = []
    tools_used: list[str] = []
    tools_unavail: list[str] = []

    py = [f for f in changed_files if f.endswith(".py")]
    js = [f for f in changed_files if f.endswith((".js", ".ts", ".tsx", ".jsx"))]

    # --- ruff ---
    ruff = probe_tool("ruff")
    if py and ruff:
        abs_py = [str(repo / f) for f in py]
        r = _run([ruff, "check", "--output-format", "json"] + abs_py, cwd=str(repo))
        tools_used.append("ruff")
        if r.stdout.strip():
            try:
                for item in json.loads(r.stdout):
                    errors.append({
                        "file": item.get("filename", ""),
                        "line": item.get("location", {}).get("row", 0),
                        "rule": item.get("code", ""),
                        "message": item.get("message", ""),
                        "tool": "ruff",
                    })
            except json.JSONDecodeError:
                pass
    elif py:
        tools_unavail.append("ruff")

    # --- biome ---
    biome = probe_tool("biome")
    if js and biome:
        abs_js = [str(repo / f) for f in js]
        r = _run([biome, "check", "--reporter", "json"] + abs_js, cwd=str(repo))
        tools_used.append("biome")
        if r.stdout.strip():
            try:
                data = json.loads(r.stdout)
                for diag in data.get("diagnostics", []):
                    errors.append({
                        "file": diag.get("file", ""),
                        "line": diag.get("line", 0),
                        "rule": diag.get("rule", ""),
                        "message": diag.get("message", ""),
                        "tool": "biome",
                    })
            except json.JSONDecodeError:
                pass
    elif js:
        tools_unavail.append("biome")

    # --- ast-grep custom rules ---
    sg = probe_tool("ast-grep") or probe_tool("sg")
    rules_dir = repo / ".ast-grep" / "rules"
    if sg and rules_dir.is_dir():
        abs_files = [str(repo / f) for f in changed_files]
        r = _run([sg, "scan", "--json", "--rule-dir", str(rules_dir)] + abs_files,
                 cwd=str(repo))
        tools_used.append("ast-grep")
        if r.stdout.strip():
            try:
                for item in json.loads(r.stdout):
                    errors.append({
                        "file": item.get("file", ""),
                        "line": item.get("range", {}).get("start", {}).get("line", 0),
                        "rule": item.get("ruleId", ""),
                        "message": item.get("message", ""),
                        "tool": "ast-grep",
                    })
            except json.JSONDecodeError:
                pass

    return {
        "pass": len(errors) == 0,
        "error_count": len(errors),
        "errors": errors,
        "tools_used": tools_used,
        "tools_unavailable": tools_unavail,
    }


# ---------------------------------------------------------------------------
# Debug tag residue check
# ---------------------------------------------------------------------------

_DEBUG_TAG_RE = re.compile(r"\[DEBUG-[a-zA-Z0-9]{4,}\]")


def debug_tag_check(changed_files: list[str], repo: Path) -> list[dict]:
    """Find ``[DEBUG-xxxx]`` tags left in *changed_files*.

    Returns list of ``{"file", "line", "content"}`` for each residual tag.
    """
    hits: list[dict] = []
    for rel in changed_files:
        p = repo / rel
        if not p.is_file():
            continue
        try:
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if _DEBUG_TAG_RE.search(line):
                    hits.append({"file": rel, "line": i, "content": line.strip()})
        except (OSError, UnicodeDecodeError):
            continue
    return hits


# ---------------------------------------------------------------------------
# Hold-the-line — filter errors to carrier-introduced lines only
# ---------------------------------------------------------------------------

def _parse_diff_ranges(repo: Path) -> dict[str, list[tuple[int, int]]]:
    """Parse ``git diff --unified=0`` to get changed line ranges per file.

    Returns ``{"path": [(start, end), ...]}`` where start/end are 1-indexed
    line numbers in the NEW version.
    """
    r = subprocess.run(
        ["git", "-C", str(repo), "diff", "--unified=0", "--no-color"],
        capture_output=True, text=True, check=False,
    )
    ranges: dict[str, list[tuple[int, int]]] = {}
    current_file: str | None = None
    hunk_re = re.compile(r"^@@ .+\+(\d+)(?:,(\d+))? @@")
    for line in r.stdout.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            if current_file not in ranges:
                ranges[current_file] = []
        elif line.startswith("@@") and current_file:
            m = hunk_re.match(line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) else 1
                if count > 0:
                    ranges[current_file].append((start, start + count - 1))
    return ranges


def hold_the_line(errors: list[dict], repo: Path) -> list[dict]:
    """Keep only errors on lines actually changed by the carrier."""
    diff_ranges = _parse_diff_ranges(repo)
    kept: list[dict] = []
    for e in errors:
        f = e.get("file", "")
        # normalize to relative path
        try:
            rel = str(Path(f).relative_to(repo))
        except ValueError:
            rel = f
        line = e.get("line", 0)
        file_ranges = diff_ranges.get(rel, [])
        if not file_ranges:
            # file not in diff — might be a pre-existing issue, skip
            continue
        if any(start <= line <= end for start, end in file_ranges):
            kept.append(e)
    return kept


# ---------------------------------------------------------------------------
# TCR gate — test && keep || revert
# ---------------------------------------------------------------------------

def tcr_gate(repo: Path, test_cmd: str | None, changed_files: list[str]) -> dict:
    """Run tests; revert changed files on failure.

    - PASS: ``{"pass": True}``
    - FAIL: ``git checkout -- <files>``, ``{"pass": False, "reverted": files, ...}``
    - SKIP: ``{"pass": None, "reason": "no test command"}``
    """
    if not test_cmd:
        return {"pass": None, "reason": "no test command"}

    r = subprocess.run(test_cmd, shell=True, capture_output=True, text=True,
                       check=False, cwd=str(repo))
    if r.returncode == 0:
        return {"pass": True, "test_cmd": test_cmd, "exit_code": 0}

    # FAIL → revert changed files (not .agent-runs/)
    revert_files = [f for f in changed_files
                    if not f.startswith(".agent-runs")]
    if revert_files:
        abs_files = [str(repo / f) for f in revert_files]
        subprocess.run(["git", "-C", str(repo), "checkout", "--"] + abs_files,
                       check=False)
    return {
        "pass": False,
        "test_cmd": test_cmd,
        "exit_code": r.returncode,
        "stderr_tail": r.stderr[-500:] if r.stderr else "",
        "reverted": revert_files,
    }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_quality_gate(changed_files: list[str], repo: Path, *,
                     test_cmd: str | None = None) -> dict:
    """Full quality gate pipeline for implementer output.

    1. fast_fix()        — auto-fix trivially fixable issues
    2. lint_check()      — structured error check
    3. hold_the_line()   — filter to carrier-introduced issues only
    4. debug_tag_check() — [DEBUG-xxxx] residue
    5. tcr_gate()        — test && keep || revert

    Returns a report dict.
    """
    if not changed_files:
        return {"quality_pass": True, "reason": "no changed files"}

    repo = Path(repo).resolve()

    # 1. deterministic auto-fix
    fix_report = fast_fix(changed_files, repo)

    # 2. lint
    lint_report = lint_check(changed_files, repo)

    # 3. hold-the-line: only carrier-introduced issues
    new_errors = hold_the_line(lint_report["errors"], repo)

    # 4. debug tags
    debug_tags = debug_tag_check(changed_files, repo)

    # 5. TCR
    tcr_report = tcr_gate(repo, test_cmd, changed_files)

    # overall pass: no new lint errors, no debug tags, tests pass (or skipped)
    lint_ok = len(new_errors) == 0
    debug_ok = len(debug_tags) == 0
    test_ok = tcr_report["pass"] is not False  # True or None (skipped)
    overall = lint_ok and debug_ok and test_ok

    return {
        "quality_pass": overall,
        "fast_fix": fix_report,
        "lint": {
            "total_errors": lint_report["error_count"],
            "new_errors_only": len(new_errors),
            "new_errors": new_errors[:20],  # cap for report size
            "tools_used": lint_report["tools_used"],
            "tools_unavailable": lint_report["tools_unavailable"],
        },
        "debug_tag_residue": debug_tags,
        "tcr": tcr_report,
    }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def self_test() -> int:
    fails: list[str] = []

    # 1. probe_tool finds something that exists
    if not probe_tool("git"):
        fails.append("probe_tool should find 'git'")
    if probe_tool("nonexistent_tool_xyzzy"):
        fails.append("probe_tool should return None for missing tools")

    # 2. debug tag regex
    assert _DEBUG_TAG_RE.search("[DEBUG-a4f2] some log"), "should match"
    assert _DEBUG_TAG_RE.search("[DEBUG-ABCD1234] tag"), "should match uppercase"
    assert not _DEBUG_TAG_RE.search("[DEBUG-ab] too short"), "should not match short"
    assert not _DEBUG_TAG_RE.search("no debug here"), "should not match absent"

    # 3. scope of hold_the_line with synthetic data
    errors = [
        {"file": "a.py", "line": 5, "rule": "E501", "message": "too long"},
        {"file": "a.py", "line": 100, "rule": "E501", "message": "too long"},
    ]
    # mock: pretend only line 5 is in a changed hunk
    import unittest.mock as mock
    fake_diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -3,0 +4,3 @@\n"
        "+new line 4\n"
        "+new line 5\n"
        "+new line 6\n"
    )
    with mock.patch("subprocess.run") as m:
        m.return_value = subprocess.CompletedProcess([], 0, stdout=fake_diff, stderr="")
        kept = hold_the_line(errors, Path("/fake"))
    assert len(kept) == 1 and kept[0]["line"] == 5, f"expected line 5 only, got {kept}"

    # 4. fast_fix with no tools installed — should degrade gracefully
    r = fast_fix(["test.py"], Path("/nonexistent"))
    assert "tools_unavailable" in r

    if fails:
        for f in fails:
            print("FAIL " + f, file=sys.stderr)
        return 1
    print("quality_gate self-test passed "
          "(probe, debug-tag regex, hold-the-line, graceful degrade).")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--self-test", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    c = sub.add_parser("check", help="Run quality gate on changed files")
    c.add_argument("--repo", required=True)
    c.add_argument("--files", nargs="+", required=True)
    c.add_argument("--test-cmd", default=None)
    args = p.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.cmd == "check":
        repo = Path(args.repo).resolve()
        result = run_quality_gate(args.files, repo, test_cmd=args.test_cmd)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["quality_pass"] else 1
    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
