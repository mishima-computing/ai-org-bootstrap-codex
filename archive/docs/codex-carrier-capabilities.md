# Codex carrier — capabilities & gotchas (reference)

Empirical findings about the Codex CLI as a carrier, gathered while debugging the
autonomous builder. Each item is marked **[verified]** (we ran it) or **[observed]**
(seen in real runs) or **[from binary]** (read from the binary / `--help`, not yet
exercised). Engine code should treat **[from binary]** as a lead, not a guarantee.

- Tested against: `codex-cli 0.137.0` (gpt-5.5 review model), macOS arm64, seatbelt sandbox.
- Date: 2026-06-19.

---

## 1. File visibility / "ignore" is SOFT, not a boundary

- Codex's default file discovery is **ripgrep-based and respects** `.gitignore`,
  `.ignore`/`.rgignore`, and `.git/info/exclude`. **[verified]**
  - Natural experiment: repos that gitignore `.agent-runs/` got clean code review
    findings; a repo that did not got the reviewer flagging the controller's own
    journals under `.agent-runs/`. **[observed]**
- **But Codex routinely bypasses ignore.** When it wants to be thorough it runs
  `rg --files -uuu` (disables all ignore), `find . -type f`, or `cat <path>`
  directly — none of which respect ignore files. Seen **even in normal `codex review`**
  (it ran `find` and `cat package-lock.json` unprompted). **[verified]**
- **Implication:** `.gitignore` / `.git/info/exclude` is a useful *default-noise
  reducer and token saver*, **not a guarantee**. Do not rely on it to keep a path
  away from a carrier. The engine writes `.agent-runs/` (+ caches, deps, sibling
  adapters) to `.git/info/exclude` in `carrier_harness._ensure_carrier_view_clean`
  for the soft win; the hard guarantee lives elsewhere (see §3, §6).

## 2. `codex review` — native, diff-anchored code review

`codex review [PROMPT]` — runs a code review non-interactively. **[verified]**

- Scope flags: `--uncommitted` (staged + unstaged + untracked), `--base <branch>`,
  `--commit <sha>`. `--uncommitted` cannot be combined with a custom `PROMPT`.
- **Not diff-limited.** The diff is the *anchor* (where to start), not the read
  ceiling. It reads the full changed file, the `HEAD` base version, **and unchanged
  dependent files**: in a test where only `lib.py` changed (dropping a parameter), it
  read the *unchanged* `caller.py` and reported the cross-file break with the exact
  `TypeError`. **[verified]**
- It will also read noise (it `cat`'d `package-lock.json`, listed `__pycache__`,
  read its own output file) — but its **final findings did not flag the noise**;
  review discipline kept findings on the real deliverable. **[verified]**
- Output is the codex transcript followed by a final review block with
  priority-tagged findings: `- [P1] <title> — <file>:<line-range>` + body.
  Semi-structured text (parseable), **not JSON** (no `--json` review mode found).
- No `-C/--cd`; must be invoked from inside the repo (cd first).
- **Implication:** strong candidate to back the `linon`/review role — diff-anchored
  scoping makes `.agent-runs/` (which is not in the code diff) a non-target, while
  cross-file context avoids the "bare diff" blind spot. Cost: re-architect the
  reviewer from a role-`.md` carrier + JSON schema to parsing review output, and fit
  the repair loop.

## 3. Hard path-level read control — permissions profiles (`deny_read`)

- Codex **does** have OS-sandbox-enforced, path-level read control — contradicting an
  earlier assumption that only coarse (workspace vs full-disk) read scope exists. **[from binary]**
  - Strings: `file_system.read` ("list of paths that need read access"),
    `Sandbox read access granted for`, `invalid deny-read glob pattern`,
    `deny-read entries`, `deny-read restrictions directly`, `deny_read`.
  - Config: a `[permissions]` table with **named profiles** (`[permissions.<name>]`),
    selected by `--permissions-profile <NAME>`. `codex sandbox --permissions-profile
    <name> <cmd>` runs a command under seatbelt with that profile.
- **Not yet made to enforce in a quick test.** Two blockers: (a) the host config
  default is `sandbox_mode = "danger-full-access"` (doctor: "filesystem unrestricted"),
  so nothing is restricted until an enforcing base is set; (b) the exact TOML field
  names/nesting inside a profile are **undocumented in `--help`** and were not nailed
  by guessing. **[verified-negative]**
- **Implication:** this is the *real* hard input-side lever for a role-differentiated
  read view (e.g. a `reviewer` profile that `deny_read`s lockfiles/generated). Needs
  the codex config reference to wire correctly + an enforcing base mode. Medium effort.

## 4. `.rules` = execpolicy (command allow/deny), NOT a file ignore

- `~/.codex/rules/*.rules` hold `prefix_rule(pattern=[...], decision="allow")` entries
  that auto-approve command *prefixes* (`git fetch`, `cargo test`, ...). `--ignore-rules`
  skips them. **[verified]**
- This governs **which commands run**, not which files are readable. It cannot
  cleanly hide a path (a file is readable via `cat`/`sed`/`nl`/`rg`/`python` — too many
  command shapes to deny). Wrong tool for view restriction.

## 5. Config & flags worth knowing

- `-c key=value` overrides **Codex** config (`~/.codex/config.toml`), parsed as TOML;
  **not** git config. So `-c core.excludesfile=...` does **not** reach git/ripgrep. **[verified]**
- `-p/--profile <name>` layers `$CODEX_HOME/<name>.config.toml` over the base config.
- `--strict-config` errors on unknown config keys (useful to probe the schema).
- `[sandbox_workspace_write]` keys: `writable_roots`, `network_access`,
  `exclude_tmpdir_env_var`, `exclude_slash_tmp`. **[from binary]**
- `codex exec` builds: `codex exec --json -C <repo> --sandbox <mode> [-o <file>] <prompt>`.

## 6. `codex exec --json` streams ITEMS, not tokens (liveness gotcha)

- `--json` emits one event per **item** (tool start/complete), **not per token**. The
  model's generation between a completed tool call and the next action is therefore a
  legitimately **silent** window with no event. **[verified]**
- On large contexts (observed ~165k input tokens) that silence routinely exceeds a
  short no-output watchdog, so a tight watchdog kills healthy carriers mid-turn. We
  raised `DEFAULT_NO_OUTPUT_TIMEOUT_SECONDS` 120 → 300 for this reason. Frozen kills
  end on an `item.completed` event (the tool finished; the model is "thinking"), not
  on a stuck command. **[observed]**
- The stdin-wait hang the harness guards against is real, but the marker
  ("Reading additional input from stdin...") also prints on **healthy exit-0 runs** —
  it is not by itself a failure signal. **[verified]**

---

## Design takeaways

1. **No single lever is a guarantee.** Stack them (defense in depth):
   gitignore/exclude (soft, default-noise) + a deterministic verdict-scope output
   filter (hard: drop reviewer findings about non-deliverable files) + role-prompt
   guards (kept) + — eventually — permissions-profile `deny_read` (hard input) and/or
   `codex review` (diff-anchored review).
2. **Prefer structure over prompt prohibitions, but keep the prompts too** — until a
   hard structural lever fully covers a case, the soft prompt stays as another layer.
3. Treat **[from binary]** items as leads requiring a verification pass before the
   engine depends on them.
