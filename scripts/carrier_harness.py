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
import os
import selectors
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# codex's signature for the stdin-wait hang this harness exists to prevent.
STDIN_HANG_MARKER = "Reading additional input from stdin"
NO_OUTPUT_TIMEOUT_ENV = "CODEX_CARRIER_NO_OUTPUT_TIMEOUT_SECONDS"
# No-output liveness watchdog. `codex --json` emits an event per ITEM (tool start/complete), not per
# token, so the model's generation between a completed tool call and the next action is a legitimate
# SILENT window. On large contexts (observed 165k input tokens) that think-time routinely exceeds 120s,
# and the old 120s watchdog killed ~9% of otherwise-healthy carriers mid-turn (frozen), stalling
# convergence. 300s clears realistic large-context model latency while still catching a genuinely stuck
# child command; the 600s hard wall remains the real ceiling, and the stdin-wait hang is already
# prevented by stdin=DEVNULL — so the tight 120s no longer earns its false positives.
DEFAULT_NO_OUTPUT_TIMEOUT_SECONDS = 300.0


def _iso8601_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _no_output_timeout_seconds() -> float:
    raw = os.environ.get(NO_OUTPUT_TIMEOUT_ENV)
    if raw is None or raw.strip() == "":
        return DEFAULT_NO_OUTPUT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_NO_OUTPUT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_NO_OUTPUT_TIMEOUT_SECONDS


def repo_carrier_discipline(repo: Path) -> str:
    # the carrier discipline ships with the ORG install (it is prepended to every carrier prompt), so
    # read it from AI_ORG_ROOT when the org runs on an external --repo. Self-hosted: org_root == repo.
    env = os.environ.get("AI_ORG_ROOT")
    base = Path(env).expanduser().resolve() if env else Path(repo)
    p = base / "bootstrap" / "carrier-discipline.md"
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def build_codex_argv(repo: Path, sandbox: str, model: str | None = None) -> list[str]:
    """The one true codex invocation. Flags are constructed here so no caller can forget them.

    `--json` makes codex print EACH EVENT to stdout as JSONL (its reasoning/work as it happens) instead
    of only human-readable progress — the agent thinking out loud, in a structured, parseable stream (the
    parity of the sibling edition's stream-json). The final deliverable is still captured via `-o`
    (output-last-message), so --json on stdout does not change result extraction; each streamed line is a
    heartbeat for the existing no-output liveness watchdog."""
    if sandbox not in {"read-only", "workspace-write", "danger-full-access"}:
        raise ValueError(f"invalid sandbox mode: {sandbox}")
    argv = ["codex", "exec", "--json", "-C", str(repo), "--sandbox", sandbox]
    if model:
        argv += ["--model", model]
    return argv


def build_codex_resume_argv(repo: Path, sandbox: str, session_id: str, model: str | None = None) -> list[str]:
    """RESUME a prior codex session by its thread id, so the agent keeps its FULL memory and only emits a
    small delta — the fix for amnesiac REPAIR re-runs (a producer/implementer re-deriving its whole proposal
    from the Linon findings alone). Flag ORDER matters: codex's global flags (--sandbox/-C/--model) come
    BEFORE the `resume` subcommand, then `resume --json <id>`; the caller appends `-o output_file` and the
    delta prompt last (resume sends the prompt as a CONTINUATION of the existing conversation)."""
    if sandbox not in {"read-only", "workspace-write", "danger-full-access"}:
        raise ValueError(f"invalid sandbox mode: {sandbox}")
    argv = ["codex", "exec", "--sandbox", sandbox, "-C", str(repo)]
    if model:
        argv += ["--model", model]
    argv += ["resume", "--json", session_id]
    return argv


def compose_prompt(prompt: str, discipline: str, prepend_discipline: bool) -> str:
    if prepend_discipline and discipline:
        return discipline.rstrip() + "\n\n---\n\n" + prompt
    return prompt


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=False, text=True, encoding="utf-8", errors="replace",
                          capture_output=True).stdout


def changed_files(repo: Path) -> list[str]:
    out = _git(repo, "status", "--porcelain")
    files = []
    for line in out.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip()
        # .agent-runs/ is the harness's own runtime scratch (logs, diff, report); it is never a
        # carrier deliverable, so it is excluded from changed-files and scope enforcement.
        if path == ".agent-runs" or path.startswith(".agent-runs/"):
            continue
        files.append(path)
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
    repo = Path(repo)
    diff = _git(repo, "diff")  # tracked changes
    # `git diff` omits untracked files; a carrier's new deliverables would be invisible in the
    # artifact/hash. Append an untracked manifest (path + sha256) so the artifact covers them too.
    untracked = [ln[3:].strip() for ln in _git(repo, "status", "--porcelain").splitlines()
                 if ln.startswith("??")]
    untracked = [u for u in untracked if not (u == ".agent-runs" or u.startswith(".agent-runs/"))]
    manifest_lines = []
    for u in sorted(untracked):
        p = repo / u
        if p.is_file():
            manifest_lines.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {u}")
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file():
                    rel = f.relative_to(repo).as_posix()
                    manifest_lines.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {rel}")
    body = diff + ("\n--UNTRACKED (sha256  path)--\n" + "\n".join(manifest_lines) + "\n"
                   if manifest_lines else "")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body, encoding="utf-8")
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return {"path": str(out_path), "sha256": sha, "bytes": len(body.encode("utf-8")),
            "untracked_count": len(manifest_lines)}


def _stream_carrier_process(argv: list[str], repo: Path, timeout: float,
                            no_output_timeout: float) -> tuple[str, str, int | None, bool, bool, bool]:
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    started = time.monotonic()
    last_output = started
    timed_out = False
    frozen = False
    killed = False

    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,   # <-- THE enforcement: codex never waits on stdin
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(repo),
    )
    selector = selectors.DefaultSelector()
    if proc.stdout is not None:
        selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
    if proc.stderr is not None:
        selector.register(proc.stderr, selectors.EVENT_READ, "stderr")

    try:
        while selector.get_map() or proc.poll() is None:
            now = time.monotonic()
            if proc.poll() is None and timeout and now - started >= timeout:
                timed_out = True
                killed = True
                proc.kill()
            elif proc.poll() is None and no_output_timeout and now - last_output >= no_output_timeout:
                frozen = True
                killed = True
                proc.kill()

            deadlines = []
            if proc.poll() is None:
                if timeout:
                    deadlines.append(timeout - (now - started))
                if no_output_timeout:
                    deadlines.append(no_output_timeout - (now - last_output))
            wait = min(max(0.0, min(deadlines)) if deadlines else 0.05, 0.05)
            events = selector.select(wait)
            if not events and proc.poll() is not None and not selector.get_map():
                break

            for key, _mask in events:
                chunk = os.read(key.fileobj.fileno(), 4096)
                if chunk:
                    if key.data == "stdout":
                        stdout_chunks.append(chunk)
                    else:
                        stderr_chunks.append(chunk)
                    last_output = time.monotonic()
                else:
                    try:
                        selector.unregister(key.fileobj)
                    except KeyError:
                        pass
                    key.fileobj.close()
    finally:
        selector.close()
        if proc.poll() is None:
            proc.kill()
            killed = True
        code = proc.wait()

    stdout = b"".join(stdout_chunks).decode("utf-8", "replace")
    stderr = b"".join(stderr_chunks).decode("utf-8", "replace")
    return stdout, stderr, code, timed_out, frozen, killed


def _carrier_view_exclude_patterns() -> list:
    """gitignore patterns for paths a carrier should never SEE: machine noise with no review value, plus
    sibling-edition adapter dirs (containment). controller_scope's scratch classes are the single source of
    truth for the cache/artifact set — we translate them to gitignore form and add dependency trees,
    coverage, and editor/OS cruft. This is a DISCOVERY filter (codex searches via gitignore-respecting rg),
    NOT a security boundary (secrets are the sandbox's job), and it deliberately omits lockfiles / generated
    output, whose visibility is role-dependent (a reviewer shouldn't read them; an implementer may update
    them) — and omits build/ dist/ target/, which can be a project's real source dirs."""
    import controller_scope as cs
    pats = [".agent-runs/"]                                            # controller runtime scratch
    pats += [f"{seg}/" for seg in cs.SCRATCH_SEGMENTS]                 # __pycache__/, .pytest_cache/, ...
    pats += [g for g in cs.SCRATCH_GLOBS if not g.endswith("/*")]      # *.pyc, *.pyo, *.pyd, *.egg-info
    pats += [f"{a}/" for a in (cs._FB1, cs._FB2)]                      # sibling-edition adapters (containment)
    pats += ["node_modules/", ".venv/", "venv/", "htmlcov/", ".coverage",
             ".DS_Store", "*.swp", ".idea/", ".vscode/"]               # deps / coverage / editor-OS cruft
    seen, out = set(), []
    for p in pats:
        if p not in seen:
            seen.add(p); out.append(p)
    return out


def _ensure_carrier_view_clean(repo: Path) -> None:
    """Keep machine noise + sibling-edition adapters OUT of the file range the carrier can see, via
    `.git/info/exclude` (NOT the tracked `.gitignore`) so it holds for ANY target repo without modifying
    its files and is shared across worktrees. Codex discovers files through gitignore-respecting search, so
    excluded == unseen. Without this a reviewer carrier free-reads the controller's own `.agent-runs/`
    journals (and caches/deps) and reviews the bookkeeping instead of the deliverable — looping on a finding
    no code change can clear (observed: a scaffold leaf failing linon r0..r3 on `.agent-runs/.../journal`).
    Idempotent; a no-op for patterns the repo already ignores."""
    try:
        gp = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-path", "info/exclude"],
                            capture_output=True, text=True, timeout=10)
        if gp.returncode != 0:
            return
        raw = gp.stdout.strip()
        excl = Path(raw) if os.path.isabs(raw) else (repo / raw)
        existing = excl.read_text(encoding="utf-8") if excl.is_file() else ""
        present = set(existing.split())
        missing = [p for p in _carrier_view_exclude_patterns()
                   if p not in present and p.rstrip("/") not in present]
        if missing:
            excl.parent.mkdir(parents=True, exist_ok=True)
            with excl.open("a", encoding="utf-8") as f:
                if existing and not existing.endswith("\n"):
                    f.write("\n")
                f.write("\n".join(missing) + "\n")
    except Exception:  # noqa: BLE001 — view-hygiene is best-effort, never break a run
        pass


def extract_token_usage(carrier_stdout: str) -> dict | None:
    """Pull the carrier's token + context spend from the codex `--json` event stream (each line a JSON
    event). codex emits a CUMULATIVE token-count event as it works; we take the final total. Returns a
    normalized dict — {input_tokens, cached_input_tokens, output_tokens, reasoning_output_tokens,
    total_tokens, context_window, context_used_percent} (a field is absent if codex didn't report it) —
    or None if the stream carried no usage. Robust to field-nesting differences across codex versions:
    the usage block is found wherever it sits (msg.info.total_token_usage, or a flat dict with
    input/output/total_tokens). This is what `/status` shows, captured for a non-interactive `exec` run."""
    best = None
    ctx_window = None
    for line in carrier_stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = ev.get("msg") if isinstance(ev.get("msg"), dict) else ev
        info = msg.get("info") if isinstance(msg.get("info"), dict) else msg
        cw = info.get("model_context_window") or msg.get("model_context_window")
        if isinstance(cw, int) and cw > 0:
            ctx_window = cw
        usage = info.get("total_token_usage") or info.get("token_usage")
        if not isinstance(usage, dict):
            usage = info if any(k in info for k in ("total_tokens", "input_tokens", "output_tokens")) else None
        if not isinstance(usage, dict):
            continue
        total = usage.get("total_tokens")
        if total is None:
            total = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
        if best is None or (total or 0) >= (best.get("total_tokens") or 0):   # cumulative -> keep the max
            best = {"input_tokens": usage.get("input_tokens"),
                    "cached_input_tokens": usage.get("cached_input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "reasoning_output_tokens": usage.get("reasoning_output_tokens"),
                    "total_tokens": total}
    if best is None:
        return None
    if ctx_window:
        best["context_window"] = ctx_window
        inp = best.get("input_tokens")
        if isinstance(inp, int) and inp >= 0:
            best["context_used_percent"] = round(100.0 * inp / ctx_window, 1)
    return {k: v for k, v in best.items() if v is not None}


def extract_session_id(carrier_stdout: str) -> str | None:
    """Pull the codex SESSION (thread) id from the `--json` event stream — codex emits a single
    `{"type":"thread.started","thread_id":"<uuid>"}` line at the start of a run. Returns that thread_id
    (so a later REPAIR iteration can RESUME this session and keep its full memory), or None if absent."""
    for line in carrier_stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "thread.started":
            tid = ev.get("thread_id")
            if isinstance(tid, str) and tid:
                return tid
    return None


def run_carrier(repo, prompt, sandbox="workspace-write", *, model=None, timeout=600,
                retries=1, prepend_discipline=True, out_dir=None, output_file=None,
                resume_session=None) -> dict:
    """Launch a Codex carrier deterministically. stdin is ALWAYS closed (the fix for the hang).
    The run is bounded by timeout; TimeoutExpired kills the process and we retry up to `retries`.
    `output_file` captures the carrier's final message via `-o` (producing carriers emit JSON there;
    the controller's schema gate validates it in Python — `--output-schema` is avoided since strict
    OpenAI schemas reject optional properties, F12)."""
    repo = Path(repo).resolve()
    _ensure_carrier_view_clean(repo)   # hide controller scratch + machine noise + sibling adapters from view
    out_dir = Path(out_dir) if out_dir else (repo / ".agent-runs" / "carrier")
    out_dir.mkdir(parents=True, exist_ok=True)
    full_prompt = compose_prompt(prompt, repo_carrier_discipline(repo), prepend_discipline)
    # RESUME a prior session (REPAIR re-runs of the producers/implementer) keeps the agent's full memory so
    # it only emits a small delta; otherwise start fresh. The prompt is still appended last (resume sends it
    # as a continuation of the existing conversation), and `-o output_file` still captures the deliverable.
    if resume_session:
        argv = build_codex_resume_argv(repo, sandbox, resume_session, model)
    else:
        argv = build_codex_argv(repo, sandbox, model)
    if output_file:
        argv += ["-o", str(output_file)]
    argv += [full_prompt]
    no_output_timeout = _no_output_timeout_seconds()

    attempts = []
    for attempt in range(retries + 1):
        timestamp = _iso8601_utc()
        # _stream_carrier_process passes stdin=subprocess.DEVNULL and streams output for liveness.
        stdout, stderr, code, timed_out, frozen, killed = _stream_carrier_process(
            argv, repo, timeout, no_output_timeout)
        log = out_dir / f"carrier-attempt{attempt}.log"
        log.write_text("timestamp: " + timestamp + "\n" + stdout +
                       ("\n--STDERR--\n" + (stderr or "")), encoding="utf-8")
        hang = STDIN_HANG_MARKER in stdout and code != 0
        usage = extract_token_usage(stdout)   # token + context spend, from codex's --json stream
        # capture this run's session id so a later REPAIR iteration can RESUME it; fall back to the id we
        # resumed (resume reuses the same thread, which codex may or may not re-announce as thread.started).
        session_id = extract_session_id(stdout) or resume_session
        attempts.append({"attempt": attempt, "timestamp": timestamp, "exit": code,
                         "timed_out": timed_out, "stdin_hang": hang, "frozen": frozen,
                         "killed": killed, "retryable": attempt < retries,
                         "no_output_timeout": no_output_timeout, "log": str(log), "usage": usage})
        if code == 0 and not timed_out and not frozen and not killed and not hang:
            return {"ok": True, "attempts": attempts, "log": str(log), "usage": usage,
                    "session_id": session_id}
        # else retry (timeout/hang/nonzero)
    return {"ok": False, "attempts": attempts, "log": attempts[-1]["log"],
            "usage": attempts[-1].get("usage"), "session_id": session_id}


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
    (out_dir / "carrier-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
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
    # 5. token/context extraction from a codex --json stream: cumulative -> final total; context %.
    stream = "\n".join([
        '{"id":"1","msg":{"type":"task_started"}}',
        '{"msg":{"type":"token_count","info":{"total_token_usage":{"input_tokens":1000,"cached_input_tokens":200,"output_tokens":50,"total_tokens":1060},"model_context_window":272000}}}',
        '{"msg":{"type":"token_count","info":{"total_token_usage":{"input_tokens":3000,"output_tokens":400,"total_tokens":3400},"model_context_window":272000}}}',
        'not json — a heartbeat line',
    ])
    u = extract_token_usage(stream)
    if not u or u.get("total_tokens") != 3400 or u.get("input_tokens") != 3000:
        fails.append(f"extract_token_usage must take the final cumulative total: {u}")
    if not u or u.get("context_window") != 272000 or "context_used_percent" not in u:
        fails.append("extract_token_usage must capture context_window + percent")
    if extract_token_usage("no events here") is not None:
        fails.append("extract_token_usage must return None when no usage is present")
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
