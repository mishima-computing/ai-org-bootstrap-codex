# Codex CLI reference (for the carrier seam)

Captured from `codex --help` / `codex exec --help` (2026-06-28) + behaviour we actually hit (the archived
`carrier_harness.py`). The AI Org runs every LLM-backed role through `codex exec` (see `ai_org/carrier.py`).
Each flag below has **what it does / when we use it / gotcha**.

## `codex` subcommands (top level)

`codex [OPTIONS] [PROMPT]` with no subcommand opens the *interactive* TUI. We never use that; we use `exec`.

| cmd | what / when |
|---|---|
| `exec` (`e`) | **run non-interactively** — the only one the carrier uses. |
| `review` | a non-interactive code review (Codex's built-in reviewer). Our maintainer/acceptance roles are our OWN reviewers, so we don't rely on this. |
| `resume` / `fork` | re-enter a saved session (keeps the agent's memory). `exec resume` is how a revise (v2) continues instead of re-deriving. |
| `apply` (`a`) | `git apply` the agent's last diff to the working tree. Relevant only if we ran codex with no write access and want to apply its patch ourselves. |
| `login`/`logout`/`mcp`/`plugin`/`app-server`/`cloud`/`doctor`/`update`/`completion`/`features` | auth, MCP, servers, maintenance — not in the per-role hot path. |

## `codex exec [OPTIONS] [PROMPT]` — the carrier

`[PROMPT]` is the instruction. **Gotcha:** if no prompt is given (or `-`), codex reads stdin and will *block*
("Reading additional input from stdin…") — the classic hang. Always pass the prompt as an arg or pipe it; the
carrier feeds `< /dev/null` when not piping. If you pipe stdin AND pass a prompt, stdin is appended as a `<stdin>`
block (handy for attaching large context under a short instruction).

### Input / output — how we get the result back
- **`-o, --output-last-message <FILE>`** — writes ONLY the agent's *final* message to FILE.
  *Use:* the clean way to capture a role's answer. We read the verdict/branch result from this file, not from stdout.
- **`--output-schema <FILE>`** — a JSON Schema the final message must match → forces *structured* output.
  *Use:* deterministic parsing — reviewer verdicts, decompose→Tasks, acceptance results. No regex on prose.
- **`--json`** — streams every event (tool start/complete, reasoning) to stdout as JSONL, live.
  *Use:* progress + the no-output watchdog keys off the event stream. *Gotcha:* the RESULT still comes from `-o`,
  not by parsing this stream; `--json` is for liveness/observability.
- `--color always|never|auto` — terminal color (irrelevant when captured).

### Model / provider
- **`-m, --model <MODEL>`** — which model the role uses. *Use:* a heavier model for hard reviews, lighter for cheap roles.
- `--oss` / `--local-provider <lmstudio|ollama>` — run against a local/open model instead of the hosted one.
- `-p, --profile <name>` — layer `$CODEX_HOME/<name>.config.toml` over the base config. *Use:* per-role config presets (model + sandbox + effort bundled under a name).

### Sandbox / permissions / working dir — the safety boundary per role
- **`-s, --sandbox <mode>`** — what model-run shell commands may do:
  - `read-only` — read the repo, run non-mutating commands; **cannot edit files**. *Use:* every REVIEW role (rfc review, acceptance, the maintainers) — they judge, they don't write.
  - `workspace-write` — edit files within the workspace (and run commands). *Use:* the **Contributor (implement)** only — the sole writer.
  - `danger-full-access` — no sandbox at all. Avoid.
- **`-C, --cd <DIR>`** — the working root codex operates in. *Use:* point each role at the right tree — the Contributor at its **isolated git worktree**, reviewers at the repo/ref under review.
- `--add-dir <DIR>` — extra writable dirs beyond the workspace. *Use:* rarely — e.g. a shared cache dir.
- `--skip-git-repo-check` — allow running outside a git repo. *Use:* only if a role runs somewhere that isn't a repo.
- `--dangerously-bypass-approvals-and-sandbox` / `--dangerously-bypass-hook-trust` — skip prompts / no sandbox / run untrusted hooks. **Dangerous**; only when the whole process is already externally sandboxed.

### Config / session
- **`-c, --config <key=value>`** — override any config value (dotted path; value parsed as TOML). *Use:* e.g. `-c model="…"`, reasoning effort, sandbox perms — without editing config.toml.
- `--enable <F>` / `--disable <F>` — toggle a feature flag (= `-c features.<F>=true/false`).
- `--strict-config` — error if config.toml has fields this codex version doesn't know. *Use:* catch config drift.
- `--ignore-user-config` — don't load `$CODEX_HOME/config.toml` (auth still uses `CODEX_HOME`). *Use:* hermetic, reproducible role runs independent of local config.
- `--ignore-rules` — don't load user/project execpolicy `.rules`.
- **`--ephemeral`** — don't persist the session to disk. *Use:* stateless one-off role calls (reviewers/acceptance) that will never be resumed. *Don't* use it for the Contributor if you want revise-via-`resume`.
- `-i, --image <FILE>…` — attach image(s) to the prompt.
- `-h, --help` · `-V, --version`.

### `codex exec resume` — continue a session (for revise / v2)
`codex exec [global flags] resume --json <session_id>` re-enters a prior run with full memory, so a revise sends
only the *delta* (the review feedback) instead of re-deriving everything. **Gotcha — flag order:** the global
flags (`--sandbox`, `-C`, `--model`) come BEFORE the `resume` subcommand; `-o <file>` is appended after.

## Gotchas we actually hit (from the archived carrier_harness)
- **stdin hang** — codex blocks waiting on stdin if no prompt source; feed `< /dev/null`. This is why a single
  carrier seam owns the subprocess.
- **no-output watchdog** — codex can stall; the seam watches the `--json` event stream and kills the run if no
  event arrives for N seconds.
- **process-group kill** — codex can exit while a grandchild still holds stdout, hanging the wait. Capture the
  pgid right after spawn (`start_new_session`) and `killpg` the whole group on timeout — don't rely on `proc.poll()`.

## Role → flags (planned)

| role (module) | sandbox | -C | output |
|---|---|---|---|
| `rfc/review` (5 + Aufheben) | read-only | repo | `--output-schema` (verdicts) + `-o` |
| `rfc/decompose` | read-only | repo | `--output-schema` (Tasks) + `-o` |
| `contribution/implement` (Contributor) | **workspace-write** | the task's **worktree** | `-o` (+ `resume` for v2) |
| `contribution/acceptance` (2-agent walkthrough) | read-only | repo | `--output-schema` (verdict) + `-o` |
| `maintainers/subsystem` · `maintainers/mainline` | read-only | repo | `--output-schema` (verdict) + `-o` |

Common: `--json` for liveness, `-o` for the result, `--ephemeral` for stateless calls, long context via piped stdin.
Only the Contributor gets `workspace-write`; every other role is read-only review.
