# ADR 0007: The AI Org owns its state — a durable Store + a rich Log

Status: accepted (implemented 2026-06-19)

## Owner / placement

The state of a goal is the **AI Org's**, not the host's. Information the org receives becomes the
org's at the moment of receipt: a goal handed to the builder (`POST /api/goal` → `controller_goal`) is
thereafter the org's state — its record, status, build (git commits), and resume. A host (Shagiri) only
**reads** that state; it never owns or writes it. The implementation lives in this repo
(`scripts/goal_store.py`, `scripts/git_ops.py`, `scripts/controller_goal.py`); the host side is a thin
reader (ADR-0009's stream + `_ai_org_state`).

## Context

Once goals can be **resumed**, the builder holds durable state. The first cut put that state in the
host (a `GoalStore` inside Shagiri's cockpit) and tried to grasp it ad hoc: a per-run dict, loose patches,
and state inferred from `git status` of an ephemeral worktree. This was wrong on several axes, surfaced
over a long design pass:

- **Ownership was inverted.** Resume is the org's behavior; the goal's state is the org's. Putting the
  store in the host meant the host owned what the org should.
- **State was scattered and unmanageable.** A run's state lived across many ephemeral worktrees (the goal
  worktree + N leaf worktrees + M stage worktrees), so reconstructing it meant crossing them. The problem
  was not the *count* of states but that they were not *managed* behind one surface.
- **The log was poor.** The Stream carried event skeletons (`leaf_done`) but not the data to reconstruct
  state (commit shas, status, wip) — a minimal-log time bomb that detonated as a need for a parallel store.
- **State is operated on, not just observed.** You `Load` a state (set git to its committed version); you
  don't only read it. A read-only mental model missed this.

## Decision

The org owns its state as **two complementary things** — an operable Store and a rich Log — and expresses
the build's structure (git scatters per Queue).

### 1. Store — durable, shared, current-state (DB-style)

`scripts/goal_store.py`. One record per goal under a **shared, durable** `.agent-runs/goals/<id>.json`
(located where `STREAM_LOG` points — the shared `.agent-runs`, never the ephemeral goal worktree), plus
the heavy work held **in git**: `refs/goals/<id>/{wip,done}` are commits (the per-leaf commit chain), in
the shared object store so they survive worktree cleanup. The record IS the current state — a host reads
it directly (it does not replay the log to know the current status).

Method surface — **CLRUD + Find** (the backend can become sqlite later without changing a caller):

- **Create** — open a goal record.
- **Load** — *operates*: makes a target git worktree BECOME the goal's state (cherry-picks the wip commit
  range). `Load(id)` — the id identifies which state; loading it sets git to that state. **Load ≠ Read.**
- **Read** — *safe*: observes the record and mutates nothing. Returns the state keyed by `state_id`.
- **Update** / **Delete** — record mutation / removal (+ the git refs).
- **Find** — 1:N lookup by various fields (`find(status="failed")`, `find(wip=sha)`): state is resolvable
  from various ids, and one id may map to many. Being able to *grasp* that there are multiple states is
  itself the value.

`save_wip` / `save_done` are the save side (one commit per converged leaf accumulates into the tip);
`load` is the inverse.

### 2. Log — the audit / observability of operations (ADR-0009)

Every operation on state is also flowed to the Stream as a `{"type":"state","op":...}` event
(create / load / save / update / delete), so the log records **what was done to state** — including a
Load. The log additionally carries the rich build data (`leaf_done` with the leaf's commit sha,
`goal_finished` with status + the wip commit), so the build is reconstructible from the log too.

**The Store is the current-state authority; the Log is history/observability. The log is not the state,
and the store is not discardable.** Both, complementary (observe-current vs replay-history).

### 3. Express the per-Queue git scattering

Git scatters per **Queue** (the Splitter's recursive task tree, ADR-0008): each leaf builds in its own
worktree and lands its own commit. The state expresses this, not just the collapsed tip: the record holds
`queue` (the split tree), `leaf_commits` (`{leaf_id → its own commit sha}` — where git scattered), and
`wip` (the linearized tip).

### Wiring

`controller_goal --goal-id` makes the org create/save its state; `--resume-from` makes it `Load` a prior
goal's state (resume is the org's behavior). The host dispatches with those flags and reads the org's
state via the shared store + log; the host no longer writes the store (its `GOALS` is transient
run-tracking holding host-only facts like delivery).

## Change history

codex (`ai-org-bootstrap-codex`):

| commit | what |
|---|---|
| `c566cb5` | per-leaf commits — PR = the request, commits = the sub-tasks |
| `ed86f74` | extract the per-leaf-commit git-state into one procedure (`git_ops`) — guards once, not inline |
| `f65447f` | the org owns its goal state (`GoalStore` in the org, not the host) |
| `badd066` | CLRUD (Load ≠ Read) + the org flows its state to a rich log |
| `f0ad652` | durable shared store (host reads current state) + state ops flow to the log |
| `2421927` | the state expresses the per-Queue git scattering (`queue` + `leaf_commits`) |

host (`shagiri`):

| commit | what |
|---|---|
| `1f81ac7` | dispatch with `--goal-id` so the org owns/writes the state |
| `6d6d4d4` | host reads `AI_Org.state` (now: the store record; the log is the op history) |
| `4b3d14b` | host stops owning state — reads the org's, doesn't write it (cockpit thinned) |

## Consequences

- The host is a thin reader/dispatcher; the double-write that collided on the shared store is removed.
- Current state is a clean read (`Read`/`Find`), history and Load-effects are in the log, resume `Load`s
  the prior state — no ad-hoc `git status` inference.
- **Open (a follow-up ADR):** the state capability is implemented in THIS edition only. It must become a
  shared capability every org edition inherits behind one `AI_Org.state` contract — re-implementing it per
  edition is not an option. And the org/host ownership of *delivery* (the org's "goal → PRs") is still
  host-side and wants the same treatment.
