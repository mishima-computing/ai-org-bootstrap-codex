# The carrier's command surface — a forensic map (and why none of it replaces the mechanism)

Companion to the README's *"Why not just use the carrier's built-in commands?"* This is the forensic backing: what
each feature **actually is** at the binary/protocol/storage level, verified against the carrier CLI **v0.137.0** on
disk (2026), so the "we use the mechanism, not the commands" claim rests on evidence, not hand-waving. Every internal
named below was read out of the shipped binary, its generated protocol schema, or its on-disk state.

## Goal mode — an app-server primitive, not a quality lever

There is **no `goal` CLI subcommand** (`codex --help` lists `exec/review/...`, no `goal`). "Goal" is a slash command +
an **app-server JSON-RPC API**, surfaced by the rich clients (app / IDE / remote-control / cloud) — **not** by
`codex exec`, which is the one-shot path this engine drives.

- **Protocol** (from `codex app-server generate-json-schema`): methods `thread/goal/set`, `thread/goal/get`,
  `thread/goal/clear`; notifications `thread/goal/updated`, `thread/goal/cleared`; types `ThreadGoal`,
  `ThreadGoalStatus`. Event `ThreadGoalUpdatedEvent`.
- **Tool handlers** in the binary: `core/src/tools/handlers/goal/create_goal.rs`, `update_goal.rs`, `get_goal.rs`. A
  **continuation loop** re-enters the model toward the goal across turns ("failed to re-read thread goal before
  continuation"), and an embedded **`goals/budget_limit.md`** template tells the model to wind down when the budget is
  hit.
- **Storage**: `~/.codex/goals_1.sqlite`, table `thread_goals(thread_id PK, goal_id, objective, status CHECK IN
  ('active','paused','blocked','usage_limited','budget_limited','complete'), token_budget, tokens_used,
  time_used_seconds, created_at_ms, updated_at_ms)`. Migration *"thread goals"* dated 2026-06-05.

**What it is:** resource governance + long-horizon orchestration of **one** carrier session — objective + token/time
budget + an evidence-gated stop. The same model, run longer and more persistently. **Why it doesn't replace the
mechanism:** this engine's goal orchestrates **many** sessions (a DAG of leaves, each independently gated) — a layer
*above* `thread_goal`. The status enum is about *running* (paused/blocked/usage_limited), never about *correctness*.
The two share a shape (objective+budget+status) at different altitudes; delegating per-leaf budget to `thread_goal`
is possible *if* the engine moved off `codex exec` onto the app-server, but it would govern resource use, not raise
per-leaf code quality.

## Code review — a real second opinion, in the same noisy class

`codex review` is a first-class subcommand (`/review` in the clients). It is a **separate reviewer pass with its own
embedded system prompt**, verified verbatim in the binary:

> `You are acting as a reviewer for a proposed code change made by another engineer.`

and a **structured verdict schema**, also verbatim:

```
"overall_correctness": "patch is correct" | "patch is incorrect",
"overall_explanation": "<1-3 sentence explanation justifying the overall_correctness verdict>",
"overall_confidence_score": ...
```

Supporting types: `ReviewTarget` (`uncommittedChanges` | `baseBranch` | `commit`/`sha`), `ReviewCodeLocation`
(`absolute_file_path`, `line_range`), `ReviewOutputEvent`, `Entered/ExitedReviewModeEvent`, and an
`auto_review_model_override` / `review_model` so review can run on a different model than the implementer.

**Why it doesn't replace the mechanism:** it is the **same class** as this engine's adversarial reviewer — an LLM
reviewer, machine-readable verdict and all. An independent 13-model × 50-PR benchmark put the best review models near
**F1 ≈ 0.5 (about half the comments false positives)**. A second same-class reviewer enlarges the *union* of catches
(decorrelated false-negatives — a genuine ensemble effect) but also enlarges the **rejection rate and triage noise**,
and it never improves the *implementer's* output. Caught-defect *certainty* comes from the deterministic,
~0-false-positive gates, which no LLM reviewer matches. Worth it only as an optional, P0/P1-filtered ensemble partner —
proven by a seeded-defect A/B, not adopted on faith.

## Execution rules (execpolicy) — a shell-command authorization gate, not a behaviour channel

The most common misread. `~/.codex/rules/*.rules` (and project `.codex/rules/`) are a **deterministic
command-execution policy** in **Starlark**:

```python
prefix_rule(pattern=["git", "push"], decision="allow")
prefix_rule(pattern=["cargo", "test"], decision="allow")
```

`decision ∈ {allow, prompt, forbidden}`, applied **most-restrictive-match** (`forbidden` > `prompt` > `allow`),
validated by `codex execpolicy check`, and amendable at runtime (`ExecPolicyAmendment`,
`acceptWithExecpolicyAmendment`); it also feeds network rules to the managed proxy. It gates **shell commands** — and
**only** shell commands.

**Why it can't carry this engine's constraints:** *"only edit these files"* and *"never emit the withheld expected
value"* are **behaviours, not commands** — there is no `prefix_rule` that expresses them. Restating them as rules is a
category error, not merely cosmetic.

## File-write scope — the sandbox's writable roots (this is the constraint that *does* map)

Carrier file edits do **not** go through execpolicy. They run through `apply_patch`, an internal tool handler
(`core/src/tools/handlers/apply_patch.rs`; the binary even notes *"apply_patch approval is not supported in exec
mode"*). The constraint that governs **which paths may be written** is the sandbox's `SandboxWorkspaceWrite` /
`writable_roots` / `sandbox_workspace_write` (sandbox modes `read-only` | `workspace-write` | `danger-full-access`;
`FileSystemSpecialPath` ∈ `project_roots`/`subpath`/`tmpdir`/`slash_tmp`).

**So the one constraint that maps cleanly is file-write scope:** launch the leaf carrier with `writable_roots`
narrowed to its `files_allowed_to_change`, and out-of-scope writes are blocked **by the sandbox** — a PREVENT, not a
detect-and-reject. That is this engine's deterministic-gate philosophy applied *earlier, inside the carrier*, and it
raises effective quality by removing a whole rejection class (scope creep) at the source rather than catching it after.
Semantic constraints (oracle leak, hardcoded expected value) have no sandbox analogue and stay with the gates + the
reviewer.

## Skills — a curated prime this engine already uses, carefully

`~/.codex/skills/<name>/SKILL.md` (YAML frontmatter `name`/`description`/`metadata` + body), installed from a curated
or arbitrary GitHub source via the system `skill-installer`. This engine's cassette layer is exactly this, kept honest
by the evidence: skills are weak for software engineering and self-generated skills can hurt, so they pay only
**curated, deterministically routed, and paired with the gate** that hard-enforces the same thing — a prime, never the
guarantee.

## The throughline

The surface is large (and there is more here than the four above — a guardian assessor, multi-agent collaboration, a
managed network proxy, memory consolidation). But for **build quality** the conclusion is uniform: the command surface
offers governance, convenience, a second noisy reviewer, and one deterministic *command*/​*write-path* gate — but **no
channel that makes the same model obey a behavioural or semantic instruction more reliably**. Quality lives in the
mechanism — an executable contract, ~0-FP gates, a withheld oracle, composed-goal acceptance, directed decomposition —
not in a richer way to ask the model nicely. The two places the carrier genuinely helps (file-write scope via
`writable_roots`; a decorrelated review ensemble) are adopted *as* mechanism, on the same terms as everything else.
