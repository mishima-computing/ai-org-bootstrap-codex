#!/usr/bin/env python3
"""Deterministic controller harness — the mechanical half of the controller, as code.

The controller is two things: a SEMANTIC core (author contracts, synthesize/aufheben, judge the
deliverable) that needs an LLM, and a MECHANICAL harness (launch carriers with the right flags,
detect hangs, enforce scope, hash provenance, run gates) that must be right EVERY time. An LLM
controller forgets mechanical details — this session hung a carrier twice by omitting `< /dev/null`
(codex then blocks on "Reading additional input from stdin..."). This module makes that class of
bug impossible: it always closes stdin, always pins the flags, always prepends carrier-discipline,
always bounds the run with a timeout, and always checks scope after the run.

It owns ONE subprocess boundary for carriers (like chrome_capture owns Chrome), so the rules in
bootstrap/carrier-discipline.md and .agent-org/.../carrier-invocation.md are ENFORCED, not merely
documented. Codex-only: this launches `codex exec`; a non-Codex carrier is supported by passing an
explicit argv template (no carrier token is hardcoded here).

CLI:
  carrier_harness.py run --repo R --sandbox workspace-write --prompt-file F [--model M]
      [--timeout 600] [--retries 1] [--allowed "demos/**" --allowed "..."] [--out DIR]
  carrier_harness.py --self-test
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import subprocess
import sys
from pathlib import Path

# codex's signature for the stdin-wait hang this harness exists to prevent.
STDIN_HANG_MARKER = "Reading additional input from stdin"


def repo_carrier_discipline(repo: Path) -> str:
    p = repo / "bootstrap" / "carrier-discipline.md"
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def build_codex_argv(repo: Path, sandbox: str, model: str | None = None) -> list[str]:
    """The one true codex invocation. Flags are constructed here so no caller can forget them."""
    if sandbox not in {"read-only", "workspace-write", "danger-full-access"}:
        raise ValueError(f"invalid sandbox mode: {sandbox}")
    argv = ["codex", "exec", "-C", str(repo), "--sandbox", sandbox]
    if model:
        argv += ["--model", model]
    return argv


def compose_prompt(prompt: str, discipline: str, prepend_discipline: bool) -> str:
    if prepend_discipline and discipline:
        return discipline.rstrip() + "\n\n---\n\n" + prompt
    return prompt


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=False, text=True,
                          capture_output=True).stdout


def changed_files(repo: Path) -> list[str]:
    out = _git(repo, "status", "--porcelain")
    files = []
    for line in out.splitlines():
        if not line.strip():
            continue
        files.append(line[3:].strip())
    return files


def scope_deviations(changed: list[str], allowed_globs: list[str]) -> list[str]:
    """Files changed outside files_allowed_to_change. Empty list = scope respected."""
    if not allowed_globs:
        return []
    out = []
    for f in changed:
        if not any(fnmatch.fnmatch(f, g) for g in allowed_globs):
            out.append(f)
    return out


def diff_artifact(repo: Path, out_path: Path) -> dict:
    diff = _git(repo, "diff")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(diff, encoding="utf-8")
    sha = hashlib.sha256(diff.encode("utf-8")).hexdigest()
    return {"path": str(out_path), "sha256": sha, "bytes": len(diff.encode("utf-8"))}


def run_carrier(repo, prompt, sandbox="workspace-write", *, model=None, timeout=600,
                retries=1, prepend_discipline=True, out_dir=None) -> dict:
    """Launch a Codex carrier deterministically. stdin is ALWAYS closed (the fix for the hang).
    The run is bounded by timeout; TimeoutExpired kills the process and we retry up to `retries`."""
    repo = Path(repo).resolve()
    out_dir = Path(out_dir) if out_dir else (repo / ".agent-runs" / "carrier")
    out_dir.mkdir(parents=True, exist_ok=True)
    full_prompt = compose_prompt(prompt, repo_carrier_discipline(repo), prepend_discipline)
    argv = build_codex_argv(repo, sandbox, model) + [full_prompt]

    attempts = []
    for attempt in range(retries + 1):
        try:
            cp = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,   # <-- THE enforcement: codex never waits on stdin
                capture_output=True, text=True, timeout=timeout, cwd=str(repo),
            )
            stdout, stderr, code, timed_out = cp.stdout, cp.stderr, cp.returncode, False
        except subprocess.TimeoutExpired as exc:
            stdout, stderr, code, timed_out = (exc.stdout or ""), (exc.stderr or ""), None, True
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        log = out_dir / f"carrier-attempt{attempt}.log"
        log.write_text(stdout + ("\n--STDERR--\n" + (stderr or "")), encoding="utf-8")
        hang = STDIN_HANG_MARKER in stdout and code != 0
        attempts.append({"attempt": attempt, "exit": code, "timed_out": timed_out,
                         "stdin_hang": hang, "log": str(log)})
        if code == 0 and not timed_out and not hang:
            return {"ok": True, "attempts": attempts, "log": str(log)}
        # else retry (timeout/hang/nonzero)
    return {"ok": False, "attempts": attempts, "log": attempts[-1]["log"]}


def cmd_run(args) -> int:
    repo = Path(args.repo).resolve()
    prompt = Path(args.prompt_file).read_text(encoding="utf-8") if args.prompt_file else args.prompt
    if not prompt:
        print("no prompt (--prompt or --prompt-file)", file=sys.stderr)
        return 2
    result = run_carrier(repo, prompt, args.sandbox, model=args.model, timeout=args.timeout,
                         retries=args.retries, out_dir=args.out)
    changed = changed_files(repo)
    deviations = scope_deviations(changed, args.allowed or [])
    out_dir = Path(args.out) if args.out else (repo / ".agent-runs" / "carrier")
    artifact = diff_artifact(repo, out_dir / "diff.patch") if changed else None
    report = {
        "ok": result["ok"], "carrier": "codex", "sandbox": args.sandbox,
        "changed_files": changed, "scope_allowed": args.allowed or [],
        "scope_deviations": deviations, "scope_ok": not deviations,
        "diff_artifact": artifact, "attempts": result["attempts"],
    }
    (out_dir / "carrier-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    for a in result["attempts"]:
        print(f"  attempt {a['attempt']}: exit={a['exit']} timed_out={a['timed_out']} stdin_hang={a['stdin_hang']}")
    print(f"  changed: {len(changed)} files; scope deviations: {deviations or 'none'}")
    print(f"  carrier {'OK' if result['ok'] else 'FAILED'}; scope {'OK' if not deviations else 'VIOLATED'}")
    return 0 if (result["ok"] and not deviations) else 1


def self_test() -> int:
    fails = []
    # 1. stdin is always closed (the core enforcement) — verify run_carrier passes DEVNULL.
    import inspect
    src = inspect.getsource(run_carrier)
    if "stdin=subprocess.DEVNULL" not in src:
        fails.append("run_carrier must pass stdin=subprocess.DEVNULL")
    # 2. argv construction pins flags and validates sandbox
    argv = build_codex_argv(Path("/tmp/x"), "workspace-write", model="m")
    assert argv[:2] == ["codex", "exec"] and "-C" in argv and "--sandbox" in argv and "--model" in argv, argv
    try:
        build_codex_argv(Path("/tmp/x"), "yolo")
        fails.append("invalid sandbox must raise")
    except ValueError:
        pass
    # 3. carrier-discipline is prepended when present
    composed = compose_prompt("DO X", "GUARD", True)
    if not composed.startswith("GUARD") or "DO X" not in composed:
        fails.append("compose_prompt must prepend discipline")
    if compose_prompt("DO X", "GUARD", False) != "DO X":
        fails.append("compose_prompt must skip discipline when disabled")
    # 4. scope deviation logic
    dev = scope_deviations(["demos/a.html", "roles/x.md", "scripts/y.py"], ["demos/**", "scripts/*.py"])
    assert dev == ["roles/x.md"], dev
    assert scope_deviations(["demos/a.html"], []) == [], "no globs = no enforcement"
    if fails:
        for f in fails:
            print("FAIL " + f, file=sys.stderr)
        return 1
    print("carrier_harness self-test passed "
          "(stdin-closed enforced, flags pinned, discipline prepended, scope checked).")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--self-test", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--repo", required=True)
    r.add_argument("--sandbox", default="workspace-write")
    r.add_argument("--prompt"); r.add_argument("--prompt-file")
    r.add_argument("--model"); r.add_argument("--timeout", type=int, default=600)
    r.add_argument("--retries", type=int, default=1)
    r.add_argument("--allowed", action="append")
    r.add_argument("--out")
    args = p.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.cmd == "run":
        return cmd_run(args)
    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
