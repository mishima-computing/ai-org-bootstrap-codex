#!/usr/bin/env python3
"""Implementer-specific harness — quality-gated carrier execution.

Wraps carrier_harness.run_carrier() with pre- and post-execution quality
checks that only make sense for the implementer role (the only carrier that
writes code).  Other carriers (designers, linon, stefan, CI writers) use
carrier_harness.py directly.

Pipeline:
  1. Pre-flight  — snapshot worktree state for potential TCR revert
  2. Carrier     — run_carrier() via carrier_harness (stdin-closed, timeout, etc.)
  3. Fast fix    — deterministic auto-fixers (ruff --fix, biome --fix)
  4. Lint check  — structured errors, hold-the-line filtering
  5. Debug tags  — [DEBUG-xxxx] residue detection
  6. TCR gate    — test && keep || revert
  7. Scope       — scope_deviations() from carrier_harness
  8. Report      — unified JSON report

CLI:
  implementer_harness.py run --repo R --prompt-file F [--sandbox workspace-write]
      [--model M] [--timeout 600] [--retries 1] [--allowed 'demos/**']
      [--test-cmd 'pytest tests/'] [--out DIR]
  implementer_harness.py --self-test
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

# Sibling modules — same scripts/ directory, no package install needed.
import carrier_harness
import quality_gate


def cmd_run(args) -> int:
    repo = Path(args.repo).resolve()
    prompt = (Path(args.prompt_file).read_text(encoding="utf-8")
              if args.prompt_file else args.prompt)
    if not prompt:
        print("no prompt (--prompt or --prompt-file)", file=sys.stderr)
        return 2

    out_dir = Path(args.out) if args.out else (repo / ".agent-runs" / "implementer")
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. Pre-flight: record worktree baseline ──────────────────────
    pre_changed = carrier_harness.changed_files(repo)

    # ── 2. Carrier execution (delegated to carrier_harness) ──────────
    result = carrier_harness.run_carrier(
        repo, prompt, args.sandbox,
        model=args.model, timeout=args.timeout, retries=args.retries,
        out_dir=out_dir,
    )

    # ── 3–6. Quality gate (implementer-specific) ─────────────────────
    post_changed = carrier_harness.changed_files(repo)
    # Only quality-check files the carrier actually changed (not pre-existing)
    carrier_changed = [f for f in post_changed if f not in pre_changed]

    if result["ok"] and carrier_changed:
        quality = quality_gate.run_quality_gate(
            carrier_changed, repo,
            test_cmd=args.test_cmd,
        )
    elif result["ok"]:
        quality = {"quality_pass": True, "reason": "no new files changed"}
    else:
        quality = {"quality_pass": None, "reason": "carrier failed, quality gate skipped"}

    # ── 7. Scope enforcement (from carrier_harness) ──────────────────
    # Re-read changed files after potential TCR revert
    final_changed = carrier_harness.changed_files(repo)
    deviations = carrier_harness.scope_deviations(final_changed, args.allowed or [])
    artifact = (carrier_harness.diff_artifact(repo, out_dir / "diff.patch")
                if final_changed else None)

    # ── 8. Unified report ────────────────────────────────────────────
    report = {
        "ok": result["ok"],
        "carrier": "codex",
        "role": "implementer",
        "sandbox": args.sandbox,
        "changed_files": final_changed,
        "scope_allowed": args.allowed or [],
        "scope_deviations": deviations,
        "scope_ok": not deviations,
        "quality_gate": quality,
        "diff_artifact": artifact,
        "attempts": result["attempts"],
    }
    report_path = out_dir / "implementer-report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    # ── Console summary ──────────────────────────────────────────────
    for a in result["attempts"]:
        print(f"  attempt {a['attempt']}: exit={a['exit']} "
              f"timed_out={a['timed_out']} stdin_hang={a['stdin_hang']}")
    print(f"  changed: {len(final_changed)} files; "
          f"scope deviations: {deviations or 'none'}")

    q = quality
    if q.get("quality_pass") is not None:
        lint_new = q.get("lint", {}).get("new_errors_only", 0)
        debug_tags = len(q.get("debug_tag_residue", []))
        tcr_pass = q.get("tcr", {}).get("pass")
        tools = q.get("fast_fix", {}).get("tools_used", [])
        tools_na = (q.get("fast_fix", {}).get("tools_unavailable", [])
                    + q.get("lint", {}).get("tools_unavailable", []))
        print(f"  quality gate: {'PASS' if q['quality_pass'] else 'FAIL'}")
        print(f"    lint errors (new): {lint_new}")
        print(f"    debug tags left:   {debug_tags}")
        print(f"    tests:             {'PASS' if tcr_pass else 'FAIL' if tcr_pass is False else 'skipped'}")
        if tools:
            print(f"    tools used:        {', '.join(tools)}")
        if tools_na:
            print(f"    tools unavailable: {', '.join(tools_na)}")

    overall = (result["ok"] and not deviations
               and q.get("quality_pass") is not False)
    print(f"  implementer {'OK' if overall else 'FAILED'}")
    return 0 if overall else 1


def self_test() -> int:
    """Verify the implementer harness wiring."""
    fails: list[str] = []

    # 1. quality_gate is importable and has the expected interface
    for fn in ("run_quality_gate", "fast_fix", "lint_check",
               "debug_tag_check", "tcr_gate", "hold_the_line"):
        if not hasattr(quality_gate, fn):
            fails.append(f"quality_gate missing {fn}")

    # 2. carrier_harness is importable and has the expected interface
    for fn in ("run_carrier", "changed_files", "scope_deviations", "diff_artifact"):
        if not hasattr(carrier_harness, fn):
            fails.append(f"carrier_harness missing {fn}")

    # 3. quality_gate self-test
    qg_rc = quality_gate.self_test()
    if qg_rc != 0:
        fails.append("quality_gate self-test failed")

    # 4. carrier_harness self-test
    ch_rc = carrier_harness.self_test()
    if ch_rc != 0:
        fails.append("carrier_harness self-test failed")

    if fails:
        for f in fails:
            print("FAIL " + f, file=sys.stderr)
        return 1
    print("implementer_harness self-test passed "
          "(quality_gate wired, carrier_harness wired, both sub-tests passed).")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--self-test", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--repo", required=True)
    r.add_argument("--sandbox", default="workspace-write")
    r.add_argument("--prompt"); r.add_argument("--prompt-file")
    r.add_argument("--model"); r.add_argument("--timeout", type=int, default=600)
    r.add_argument("--retries", type=int, default=1)
    r.add_argument("--allowed", action="append")
    r.add_argument("--test-cmd", default=None,
                   help="Test command for TCR gate (e.g. 'pytest tests/')")
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
