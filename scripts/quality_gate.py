#!/usr/bin/env python3
"""Implementer post-edit quality gate — deterministic, fail-closed, bounded (clean reimplementation).

Runs after the implementer carrier edits files and BEFORE scope enforcement (fast_fix mutates).
Checks ONLY the carrier's changed files. Design principles (the prior borrowed version failed each):

- **Fail-closed tooling.** A tool that is *unavailable* is skipped. A tool that is *invoked and then
  errors / times out / emits unparseable output* FAILS CLOSED — it never degrades to pass (NN4).
- **Bounded.** Every subprocess has a timeout; a hung linter cannot wedge the controller (NN4).
- **New files fully linted.** Untracked (new) files keep ALL lint errors; edited tracked files keep
  errors on changed lines. (Disclosed limit: a new error on an UNCHANGED line of an edited file —
  e.g. F401 after deleting an import's only use — is not attributed; full before/after diagnostic
  comparison is the follow-up. NN3.)
- **No hidden mutation beyond auto-fix.** There is NO test-commit-revert here: running tests and
  deciding revert/block is the controller loop's job, not a destructive `git checkout` hidden in a
  gate (the borrowed TCR left untracked files behind and mis-reported reverts).

CLI:  quality_gate.py check --repo R --files f1 f2 ...   |   quality_gate.py --self-test
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

TOOL_TIMEOUT = 120
# Broad on purpose: case-insensitive, -/_/: separators, >=2 trailing chars. Catches [DEBUG-1234],
# [debug_x9], [Debug:tmp]. Targeted to bracketed tags to avoid flagging the plain word "debug".
DEBUG_TAG_RE = re.compile(r"\[debug[-_:][\w.\-]{2,}\]", re.I)


def probe(name: str) -> str | None:
    return shutil.which(name)


def _run(argv: list[str], cwd: Path, timeout: int = TOOL_TIMEOUT):
    """Return (returncode, stdout, stderr, timed_out). Bounded; stdin closed."""
    try:
        cp = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, check=False,
                            timeout=timeout, stdin=subprocess.DEVNULL)
        return cp.returncode, cp.stdout or "", cp.stderr or "", False
    except subprocess.TimeoutExpired:
        return 124, "", "timeout", True


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=False, timeout=60).stdout


def _changed_line_ranges(repo: Path) -> dict[str, list[tuple[int, int]]]:
    """Changed line ranges per tracked file from `git diff HEAD --unified=0` (staged + unstaged)."""
    out = _git(repo, "diff", "HEAD", "--unified=0", "--no-color")
    ranges: dict[str, list[tuple[int, int]]] = {}
    cur = None
    hunk = re.compile(r"^@@ .+\+(\d+)(?:,(\d+))? @@")
    for line in out.splitlines():
        if line.startswith("+++ b/"):
            cur = line[6:]
            ranges.setdefault(cur, [])
        elif line.startswith("@@") and cur:
            m = hunk.match(line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) else 1
                if count > 0:
                    ranges[cur].append((start, start + count - 1))
    return ranges


def _untracked(repo: Path) -> set[str]:
    return {l for l in _git(repo, "ls-files", "--others", "--exclude-standard").splitlines() if l}


def debug_tag_check(changed_files: list[str], repo: Path) -> dict:
    hits, skipped = [], []
    for rel in changed_files:
        p = repo / rel
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")  # non-UTF8 still scanned
        except OSError:
            skipped.append(rel)
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if DEBUG_TAG_RE.search(line):
                hits.append({"file": rel, "line": i, "content": line.strip()[:120]})
    return {"hits": hits, "skipped": skipped}


def fast_fix(changed_files: list[str], repo: Path) -> dict:
    py = [f for f in changed_files if f.endswith(".py")]
    js = [f for f in changed_files if f.endswith((".js", ".ts", ".tsx", ".jsx"))]
    used, unavail, failed = [], [], []
    ruff = probe("ruff")
    if py and ruff:
        rc1, _, _, t1 = _run([ruff, "check", "--fix", "--exit-zero", *[str(repo / f) for f in py]], repo)
        rc2, _, _, t2 = _run([ruff, "format", *[str(repo / f) for f in py]], repo)
        used.append("ruff")
        if t1 or t2 or rc2 != 0:
            failed.append("ruff")
    elif py:
        unavail.append("ruff")
    biome = probe("biome")
    if js and biome:
        _, _, _, t = _run([biome, "check", "--fix", *[str(repo / f) for f in js]], repo)
        used.append("biome")
        if t:
            failed.append("biome")
    elif js:
        unavail.append("biome")
    return {"tools_used": used, "tools_unavailable": unavail, "tools_failed": failed}


def _collect(tool: str, argv, repo, parse, errors, used, failed,
             ok_codes=(0, 1)):
    """Run a lint tool fail-closed: timeout / unexpected exit / unparseable output → tools_failed."""
    rc, out, err, to = _run(argv, repo)
    used.append(tool)
    if to or rc not in ok_codes:
        failed.append(tool)
        return
    if out.strip():
        try:
            parse(out, errors)
        except (json.JSONDecodeError, KeyError, TypeError):
            failed.append(tool)  # invoked but unparseable → fail closed


def lint_check(changed_files: list[str], repo: Path) -> dict:
    errors: list[dict] = []
    used: list[str] = []
    unavail: list[str] = []
    failed: list[str] = []
    py = [f for f in changed_files if f.endswith(".py")]
    js = [f for f in changed_files if f.endswith((".js", ".ts", ".tsx", ".jsx"))]

    ruff = probe("ruff")
    if py and ruff:
        def parse_ruff(out, errs):
            for it in json.loads(out):
                errs.append({"file": it.get("filename", ""),
                             "line": it.get("location", {}).get("row", 0),
                             "rule": it.get("code", ""), "message": it.get("message", ""), "tool": "ruff"})
        _collect("ruff", [ruff, "check", "--output-format", "json", *[str(repo / f) for f in py]],
                 repo, parse_ruff, errors, used, failed)
    elif py:
        unavail.append("ruff")

    biome = probe("biome")
    if js and biome:
        def parse_biome(out, errs):
            for diag in json.loads(out).get("diagnostics", []):
                errs.append({"file": diag.get("file", ""), "line": diag.get("line", 0),
                             "rule": diag.get("rule", ""), "message": diag.get("message", ""), "tool": "biome"})
        _collect("biome", [biome, "check", "--reporter", "json", *[str(repo / f) for f in js]],
                 repo, parse_biome, errors, used, failed)
    elif js:
        unavail.append("biome")

    # hold-the-line: new (untracked) files keep ALL errors; edited files keep changed-line errors.
    ranges = _changed_line_ranges(repo)
    untracked = _untracked(repo)
    kept = []
    for e in errors:
        f = e.get("file", "")
        try:
            rel = str(Path(f).relative_to(repo))
        except ValueError:
            rel = f
        if rel in untracked:
            kept.append(e)
            continue
        if any(s <= e.get("line", 0) <= en for s, en in ranges.get(rel, [])):
            kept.append(e)
    return {"errors": kept, "all_error_count": len(errors), "tools_used": used,
            "tools_unavailable": unavail, "tools_failed": failed}


def run_quality_gate(changed_files: list[str], repo: Path) -> dict:
    repo = Path(repo).resolve()
    if not changed_files:
        return {"quality_pass": True, "reason": "no changed files"}
    fix = fast_fix(changed_files, repo)
    lint = lint_check(changed_files, repo)
    dbg = debug_tag_check(changed_files, repo)
    tools_failed = sorted(set(fix["tools_failed"]) | set(lint["tools_failed"]))
    quality_pass = (not lint["errors"]) and (not dbg["hits"]) and (not tools_failed)
    return {
        "quality_pass": quality_pass,
        "fast_fix": fix,
        "lint": {"new_errors": lint["errors"][:20], "new_error_count": len(lint["errors"]),
                 "tools_used": lint["tools_used"], "tools_unavailable": lint["tools_unavailable"]},
        "debug_tags": dbg["hits"], "debug_skipped": dbg["skipped"],
        "tools_failed": tools_failed,
    }


def self_test() -> int:
    fails = []
    if not probe("git"):
        fails.append("probe should find git")
    if probe("nonexistent_xyzzy"):
        fails.append("probe should miss nonexistent tool")
    # debug regex: broad (case/sep variants) but not the bare word
    for good in ["x  # [DEBUG-1234]", "[debug_ab]", "[Debug:tmp9]"]:
        if not DEBUG_TAG_RE.search(good):
            fails.append(f"debug regex should match {good!r}")
    for bad in ["debugging the thing", "# normal comment"]:
        if DEBUG_TAG_RE.search(bad):
            fails.append(f"debug regex should NOT match {bad!r}")
    # _collect fail-closed: a tool that exits 2 with no output → failed
    errs, used, failed = [], [], []
    _collect("toolx", [sys.executable, "-c", "import sys;sys.exit(2)"], Path("."),
             lambda o, e: None, errs, used, failed)
    if "toolx" not in failed:
        fails.append("_collect must fail closed on unexpected exit")
    # _collect fail-closed: unparseable output on exit 0
    errs, used, failed = [], [], []
    _collect("tooly", [sys.executable, "-c", "print('not json')"], Path("."),
             lambda o, e: json.loads(o), errs, used, failed)
    if "tooly" not in failed:
        fails.append("_collect must fail closed on unparseable output")
    if fails:
        for f in fails:
            print("FAIL " + f, file=sys.stderr)
        return 1
    print("quality_gate self-test passed (probe, broad debug regex, fail-closed tool handling).")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--self-test", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    c = sub.add_parser("check")
    c.add_argument("--repo", default=".")
    c.add_argument("--files", nargs="*", default=[])
    c.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.cmd == "check":
        report = run_quality_gate(args.files, Path(args.repo))
        print(json.dumps(report, indent=2, ensure_ascii=False) if args.json
              else f"quality_pass={report['quality_pass']} "
                   f"lint={report.get('lint', {}).get('new_error_count')} "
                   f"debug={len(report.get('debug_tags', []))} failed={report.get('tools_failed')}")
        return 0 if report["quality_pass"] else 1
    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
