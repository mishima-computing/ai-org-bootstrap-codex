# judge

The deterministic judge of the AI Org's court. It adjudicates provenance
disputes — **which carrier (Codex) session produced which committed body
text, when, reading what, with what recorded deliberation** — purely from
the records: the vessel's git history, the run logs under
`<vessel>/.ai-org/log/runs`, and the carrier rollouts under
`~/.codex/sessions`. It renders no opinions; it renders the record.
**No LLM anywhere**: every link is deterministic string/JSON processing,
so the same inputs always produce the same report.

Read-only by construction: it never creates, modifies, or deletes anything
inside vessel repos, run logs, or `~/.codex`. Python stdlib only. Rollout
layout knowledge is reused from `carrier_revive.scanner`, not re-derived.

## The provenance chain

```
field ──▶ version ──▶ run ──▶ carrier call ──▶ session ──▶ recorded thinking
```

1. **field → version** — resolve the target (a `--term` occurrence or a
   `--node` json-path) against the branch-tip body; map it to the nearest
   enclosing node carrying an `"id"` (plan bodies tag semantic nodes such as
   `constraint:hard:28`); walk `git log` over the body to the commit that
   introduced/last changed that node; corroborate the version/round from the
   commit subject and the committed
   `patch-series-review-rounds/*revision-delta*.json` /
   `*response-record*.json` records that list the node id.
2. **version → run** — the run under `.ai-org/log/runs/*/run-*/` whose
   `supervisor.jsonl` span covers the commit time and whose stream mentions
   the series id; step identity from `approach.step.started/completed`
   windows, or (reform/revision runs, which emit none) from the
   `-o .../patch_series-<step>.json` element of the codex argv.
3. **run → carrier call** — the `subprocess.completed` codex event whose
   captured stdout artifact contains the target text (raw and JSON-escaped
   forms both checked). Payloads larger than the inline cap arrive as
   `{"truncated": ...}` and are resolved from
   `artifacts/payloads/<event_id>.json`.
4. **call → session** — evidence tiers, strongest first:
   `session-id+content` (codex prints `session id: <uuid>` on stderr, which
   the run log captured, **and** the call's output text is found inside that
   rollout) → `session-id` → `content` (unique content overlap) →
   `cwd+time` (weak, reported as such) → `none`.
5. **session → thinking** — mechanical extraction from the rollout, verbatim
   with line numbers: the agent-side items containing the target text
   (anchors), reasoning items around them (quoted when plaintext; explicitly
   reported as `encrypted_content` when codex ran with reasoning summaries
   off), inter-agent/sub-agent messages, and the file-read tool calls
   immediately preceding the anchor (what the author had just read).

A link that cannot be resolved is reported as **broken with the exact
reason** — the chain never guesses past a gap.

## Library API (primary)

```python
import judge

report = judge.trace(
    "/tmp/engine_cue_phase2",
    "ai-org/patch-series/ai-org-engine-cue-canonical-bodies",
    "technical-approach-plan.json",
    term="six-state",              # or node="/problem/constraints/hard/26"
)                                   # -> ProvenanceReport (plain dataclasses)
report.broken_link                  # "" when fully resolved, else link name
report.session.uuid                 # e.g. "019f4f12-c389-..."
report.session.confidence           # "session-id+content" ...
report.thinking.items               # verbatim excerpts with rollout line nos
report.to_dict()                    # JSON-safe dict of the whole chain

calls = judge.calls(vessel, run_dir_or_latest)   # -> list[CarrierCall]
```

## CLI (thin printer over the library)

```sh
python -m judge trace <vessel> <branch> <body-path> \
    [--node <json-path>] [--term <text>] [--days N] [--json]
python -m judge calls <vessel> <run-dir-or-latest> [--json] [--no-sessions]
```

`trace` prints one section per chain link (paths, ids, timestamps,
confidence) and a final "recorded thinking" section with quotes; exit code
is 1 when any link is broken. `calls` prints the building-block view: every
codex call of a run with step, payload/stdout artifacts, matched session
uuid and confidence.

## Module map

| module        | chain link |
|---------------|------------|
| `body.py`     | position-tracking JSON scanner: term offset → json path → id-bearing node |
| `gitread.py`  | read-only git plumbing (`log`, `show`, `ls-tree`) |
| `version.py`  | field → version (+ committed round-record corroboration) |
| `runlog.py`   | version → run → call (supervisor parsing, spill resolution, step attribution) |
| `rollout.py`  | call → session → thinking (reuses `carrier_revive.scanner`) |
| `trace.py`    | chain orchestration (`judge.trace`, `judge.calls`) |
| `report.py`   | human-readable rendering |
| `__main__.py` | CLI wrapper |

JSON bodies today; the same chain applies to CUE bodies later — only the
target-resolution layer in `body.py` needs a CUE-aware sibling.
