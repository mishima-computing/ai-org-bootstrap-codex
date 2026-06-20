#!/usr/bin/env python3
"""Black-box property fuzzing for CLI deliverables — ADR-0009 #3 (robustness oracle, the edge-case half).

Judgment defines the INVARIANTS that must hold for ANY input; deterministic machinery generates adversarial
inputs, searches for a counterexample, and MINIMIZES it. The invariants, regardless of the specific input:

  - NO CRASH — the CLI must EXIT with a code, never die with an uncaught exception / traceback or a
    signal-kill (segfault / OOM). Garbage in must produce a handled error, not a stack dump.
  - EXIT IN POLICY — when the contract declares its exit-code set (status_and_errors), every exit is in it.
  - NO HANG — the run must not time out.

Each violation becomes a finding carrying a MINIMIZED, replayable input (the research's "minimized
counterexample"), bounded so the finding stays legible. This is generator-based black-box fuzzing of a
subprocess — the right tool for a CLI deliverable, distinct from in-process Hypothesis or coverage-guided
Atheris (deeper backends for a future box image). Generation is SEEDED so a failure reproduces.

The boundary (ADR-0009): the contract's error model (which inputs are valid, what code each error class
returns) is the model's judgment; the search for an input that breaks it is deterministic.
"""
from __future__ import annotations

import random
from typing import Optional

# adversarial argument tokens — empty, help, over-deep paths, separators, control chars, oversized, unicode.
_ARG_SEEDS = [
    "", "--help", "-h", "a", "a.b", "a.b.c.d.e.f.g", "." * 256, "-", "--", "----", "- -",
    "a b c", "'quoted'", '"q"', "\t", "a\x00b", "あ.い", "../../etc", "%s%n", "{}", "x" * 4096,
]
# adversarial stdin payloads — empty, partial/!valid JSON, wrong types, deep nesting, binary, oversized.
_STDIN_SEEDS = [
    "", "{", "{bad", "[", "]", "null", "123", '"x"', "{}", "[]", "true", "\x00\x01\xff",
    '{"a":' * 40 + "1" + "}" * 40, "{" * 2000, '{"a":{"b":', "not json at all", "A" * 200000,
    '{"a": "\\ud800"}', "\n\n\n", "  ", '{"k":' + '"' + "z" * 5000 + '"}',
]

_TRACEBACK = "Traceback (most recent call last)"
_TIMEOUT_CODE = 124            # the bounded runner's timeout marker
_SIGNAL_CODES = {134, 135, 136, 137, 138, 139}   # abort / bus / SIGKILL(OOM) / segfault, etc.


def _bounded(s: str, n: int = 120) -> str:
    s = s or ""
    return s if len(s) <= n else f"{s[:n]}…(+{len(s) - n} chars)"


def _declared_codes(profile: dict) -> set:
    se = profile.get("status_and_errors") or {}
    codes = set()
    for key in ("success_codes", "invalid_input_codes", "operational_failure_codes"):
        codes.update(se.get(key) or [])
    return codes


def _violation(res, declared: set):
    """Return (kind, severity, detail) for the first invariant this run breaks, else None."""
    if res.returncode == _TIMEOUT_CODE:
        return ("hang", "major", "the run timed out (possible infinite loop / unbounded read)")
    if _TRACEBACK in (res.stderr or "") or res.returncode < 0 or res.returncode in _SIGNAL_CODES:
        how = "uncaught exception (traceback)" if _TRACEBACK in (res.stderr or "") else \
              f"signal-kill exit {res.returncode}"
        return ("crash", "critical", f"the CLI crashed instead of exiting cleanly: {how}")
    if declared and res.returncode not in declared:
        return ("exit_out_of_policy", "major",
                f"exit {res.returncode} is outside the declared status_and_errors {sorted(declared)}")
    return None


def _full_command(entry: str, arg: str) -> str:
    arg = (arg or "").strip()
    return entry.strip() if not arg else f"{entry.strip()} {arg}"


def _minimize_stdin(entry, arg, stdin, runner, declared, cwd, kind):
    """Shrink stdin while the SAME violation kind persists (ddmin-lite): try empty, then halves, then a
    trailing/leading trim. Returns the smallest stdin that still breaks the invariant the same way."""
    def still(s):
        v = _violation(runner(_full_command(entry, arg), cwd=cwd, stdin=s), declared)
        return v and v[0] == kind

    best = stdin or ""
    for cand in ("", best[: len(best) // 2], best[len(best) // 2:]):
        if cand != best and still(cand):
            best = cand
    # linear trim from the end while the violation holds (cheap, bounded)
    step = max(1, len(best) // 8)
    while len(best) > step and still(best[: len(best) - step]):
        best = best[: len(best) - step]
    return best


def fuzz(profile: dict, runner, *, cwd: Optional[str] = None, iterations: int = 40, seed: int = 1729) -> dict:
    """Fuzz the CLI described by `profile` via the injectable `runner`. Returns
    {applicable, passed, findings, checks_run}. A finding carries the minimized (arg, stdin) counterexample,
    bounded. Deduped by (kind, arg) so one robustness bug is one finding, not forty."""
    entry = (profile.get("entrypoint") or {}).get("invocation")
    if not entry:
        return {"applicable": False, "passed": True, "findings": [], "checks_run": 0}
    declared = _declared_codes(profile)
    rng = random.Random(seed)

    cases = [(a, s) for a in _ARG_SEEDS for s in (("", "{bad", "A" * 200000))][:24]
    cases += [(rng.choice(_ARG_SEEDS), rng.choice(_STDIN_SEEDS)) for _ in range(iterations)]

    findings, seen = [], set()
    for arg, stdin in cases:
        res = runner(_full_command(entry, arg), cwd=cwd, stdin=stdin)
        v = _violation(res, declared)
        if not v:
            continue
        kind, severity, detail = v
        key = (kind, arg)
        if key in seen:
            continue
        seen.add(key)
        min_stdin = _minimize_stdin(entry, arg, stdin, runner, declared, cwd, kind)
        findings.append({
            "source": "cli-fuzz", "check": kind, "severity": severity, "passed": False, "detail": detail,
            "arg": _bounded(arg, 80), "stdin": _bounded(min_stdin), "stdin_len": len(stdin),
            "returncode": res.returncode,
        })
    return {"applicable": True, "passed": not findings, "findings": findings, "checks_run": len(cases)}
