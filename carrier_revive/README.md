# carrier_revive

Revive dead Codex carrier sessions against their work trees.

After an abnormal carrier death (usage limit, kill, reboot), an operator picks
a work tree / session and this module resumes the carrier's Codex session
headlessly, so in-flight work continues with full conversational context.

Stdlib only. `python -m carrier_revive <cmd>`.

## Usage

```
python -m carrier_revive list [--days N]          # scan rollouts, newest first (default N=2)
python -m carrier_revive show <uuid-prefix>       # one session's detail
python -m carrier_revive revive <uuid-prefix|--last> [--prompt TEXT] [--dry-run] [--detach]
```

`list` columns: short uuid, start time, age of last rollout activity (file
mtime), cwd exists (`ok`/`GONE`), live (`yes`/`-`), `wt` when the cwd is an
engine-preserved implementer worktree (`$TMPDIR/ai-org-patch-author-*/worktree`),
first user-prompt snippet, full cwd.

`revive` default prompt: `Continue where you left off.`
`--detach` runs the equivalent of `nohup ... </dev/null >>log 2>&1 &` (new
session via `start_new_session`) and prints the pid and the log path; the log
file `carrier-revive-<short-uuid>-<ts>.log` is created inside the target cwd.
`--dry-run` prints the exact command and cwd without running anything.

## Verified semantics (live experiments, 2026-07-11, codex-cli 0.144.1)

- **E1 — resume runs in the invoker's cwd, not the recorded one.**
  A toy session recorded in dir A was resumed from dir B; the newly requested
  file landed in **dir B**, while conversational context from dir A (contents
  of a file written in step 1) was fully remembered. Therefore `revive`
  launches codex with `cwd` set to the session's **recorded** cwd
  (`payload.cwd` from the rollout's `session_meta` line). This is the load-
  bearing behavior of the whole tool.
- **E2 — resume-by-uuid needs no `--all` from a foreign cwd.**
  `codex exec resume <uuid>` resolved and resumed the session from an
  unrelated directory without `--all`. (`--all` only widens `--last` picking,
  which is cwd-filtered by default.) So the constructed command never includes
  `--all`.
- **Bonus:** resuming **appends** to the same rollout file (no fork); the same
  uuid stays valid for repeated revives.
- `codex exec resume` has no `-s/--sandbox` flag; sandbox overrides would go
  via `-c sandbox_mode=...`. This tool does not override sandbox settings —
  the resumed session runs with the user's configured defaults.
- Codex refuses to run in a non-trusted (non-git) directory, so
  `--skip-git-repo-check` is appended **only** when the recorded cwd is not
  inside a git repository (detected by walking up for a `.git` dir or file).

## Safety gates (enforced in order)

1. **(a) Unique resolution** — the uuid prefix must match exactly one scanned
   session; ambiguity or no-match exits 2 with the candidates listed.
2. **(b) Never double-drive** — if the session appears live (see heuristic
   below), revive refuses with exit 3, even under `--dry-run`.
3. **(c) cwd must exist** — if the recorded cwd is gone, revive prints the
   preserved-worktree candidates (`$TMPDIR/ai-org-patch-author-*/worktree`)
   and exits 4. It never guesses a replacement cwd.
4. **(d) Launch** — `codex exec resume <uuid> "<prompt>"` with cwd = the
   recorded cwd, `--skip-git-repo-check` only outside git repos.

## Liveness heuristic (best-effort, documented)

A session counts as live when a running process whose command line contains
the word `codex` matches any of:

- **(A)** the full session uuid appears in the command line
  (`codex exec resume <uuid> ...`);
- **(B)** the command line declares the session's recorded cwd via
  `-C <dir>` / `--cd <dir>` — verified live: the ai-org engine launches
  carriers as `codex exec ... -C /tmp/engine_cue_phase2 ...` from a different
  process cwd, so (C) alone would miss them;
- **(C)** the process's actual cwd (via `lsof -a -p PID -d cwd`) equals the
  recorded cwd.

Path comparison tolerates the macOS `/tmp` -> `/private/tmp` alias. False
negatives are possible (ps/lsof are best-effort); sessions sharing a cwd with
any running codex all read as live — deliberately conservative, since gate (b)
prefers refusing over double-driving.

## Never

- Kills or signals any process (there is no kill code in the package;
  pid 10882 is additionally on a hard do-not-touch list).
- Writes under `~/.codex/sessions` (strictly read-only).
- Writes anywhere except its own `--detach` log file inside the target cwd.

## Tests

```
python -m pytest
```

Tests cover the pure parts only — rollout parsing, uuid resolution, command
construction, gate ordering, liveness parsing — with fake ps output and
tmp_path filesystems. No live codex is ever invoked by tests.
