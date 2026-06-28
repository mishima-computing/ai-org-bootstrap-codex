# Codex CLI reference (for the carrier seam)

Captured from `codex --help` / `codex exec --help` on 2026-06-28. The AI Org runs every LLM-backed role
through `codex exec` (see `ai_org/carrier.py`). This is the reference for which flags each role uses.

## `codex` subcommands

`codex [OPTIONS] [PROMPT]` (no subcommand → interactive). Subcommands:

| cmd | what |
|---|---|
| `exec` (alias `e`) | **run Codex non-interactively** ← what the carrier uses |
| `review` | run a code review non-interactively |
| `login` / `logout` | manage auth |
| `mcp` / `mcp-server` | manage / serve MCP |
| `plugin` | manage plugins |
| `app-server` / `remote-control` / `exec-server` | (experimental) servers |
| `app` | launch desktop app |
| `resume` / `fork` / `archive` / `unarchive` / `delete` | session management |
| `apply` (alias `a`) | `git apply` the latest agent diff to the working tree |
| `sandbox` | run a command inside Codex's sandbox |
| `cloud` | (experimental) Codex Cloud tasks |
| `completion` / `update` / `doctor` / `debug` / `features` / `help` | misc |

## `codex exec [OPTIONS] [PROMPT]` — non-interactive (the carrier)

`[PROMPT]` — initial instructions. If omitted or `-`, read from stdin. If stdin is piped AND a prompt
is given, stdin is appended as a `<stdin>` block.

Sub: `resume` (resume a session by id / `--last`), `review`, `help`.

### Input / output
- `-o, --output-last-message <FILE>` — write the agent's final message to FILE. **← clean output capture**
- `--output-schema <FILE>` — JSON Schema describing the model's final response shape. **← structured output**
- `--json` — print events to stdout as JSONL.
- `--color always|never|auto` (default auto).

### Model / provider
- `-m, --model <MODEL>`
- `--oss` — use an open-source provider; `--local-provider <lmstudio|ollama>`
- `-p, --profile <name>` — layer `$CODEX_HOME/<name>.config.toml` on the base config

### Sandbox / permissions / working dir
- `-s, --sandbox <read-only | workspace-write | danger-full-access>` **← per-role**
- `-C, --cd <DIR>` — working root **← the role's worktree**
- `--add-dir <DIR>` — extra writable dirs alongside the workspace
- `--skip-git-repo-check` — allow running outside a git repo
- `--dangerously-bypass-approvals-and-sandbox` — skip all prompts, no sandbox (EXTREMELY DANGEROUS)
- `--dangerously-bypass-hook-trust` — run hooks without persisted trust (DANGEROUS)

### Config
- `-c, --config <key=value>` — override config (dotted path, value parsed as TOML; e.g. `-c model="o3"`)
- `--enable <FEATURE>` / `--disable <FEATURE>` — feature flags (= `-c features.<name>=true/false`)
- `--strict-config` — error on unrecognized config fields
- `--ignore-user-config` — do not load `$CODEX_HOME/config.toml` (auth still uses `CODEX_HOME`)
- `--ignore-rules` — do not load user/project execpolicy `.rules`
- `--ephemeral` — do not persist session files to disk **← stateless role calls**
- `-i, --image <FILE>...` — attach image(s) to the prompt
- `-h, --help` · `-V, --version`

## Role → flags (planned)

| role (module) | sandbox | cd | output |
|---|---|---|---|
| `rfc/review` (5 + Aufheben) | read-only | repo | `--output-schema` (verdicts) + `-o` |
| `rfc/decompose` | read-only | repo | `--output-schema` (Tasks) + `-o` |
| `contribution/contributor` (implement) | **workspace-write** | the task's **worktree** (`-C`) | `-o` |
| `contribution/acceptance` (2-agent walkthrough) | read-only | repo | `--output-schema` (verdict) + `-o` |
| `maintainers/subsystem` / `maintainers/mainline` | read-only | repo | `--output-schema` (verdict) + `-o` |

Common: `--ephemeral` for stateless calls; long prompts via stdin; `--json` or `-o` to capture output.
Only the Contributor writes code (workspace-write); every other role is read-only review.
