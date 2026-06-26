# PR1 — within-batch frontier-leaf parallelism (converged build plan)

Durable record (two-carrier converged). Goal: run a wave of READY sibling leaves CONCURRENTLY instead of serially, safely
and conservatively, reusing infrastructure already proven in this codebase. NOT in scope: the OCC generation fence
(controller_attempts.py is a red herring at this level — it fences the per-leaf design-wave cohort, not sibling leaves;
deferred to PR3), read/write-set split, cross-batch optimistic merge (PR2).

## What PR1 builds

1. **Wave executor** — replace the SERIAL leaf loop at `controller_goal.py:~1048-1195` with a `ThreadPoolExecutor`
   wave-executor over `frontier.ready_tasks(plan)`, bounded by `AI_ORG_MAX_PARALLEL` (`controller_goal.py:~219`). Reuse
   the **thread-pool-then-serial-fold pattern already proven** in `_run_wave_parallel` (the design-wave parallelism). A
   wave's ready leaves are textually disjoint because `frontier.ready_tasks` already rejects scope-conflicting siblings.
   Empty/absent scope is a repo-wide conflict, so an unconstrained writer runs alone and is never co-scheduled with a
   sibling. Completed futures release dependents into the next wave via the existing `frontier.advance` / `ready_tasks`
   loop. A leaf crash/exception in one future must not corrupt siblings (each runs in its own worktree) and must not
   escape the executor — it is folded as that leaf's failure.

2. **Merge-as-serial-fold (the "merge lock")** — `git_ops.merge_and_commit_leaf` writes the shared worktree index/HEAD,
   so concurrent merges race (`index.lock`). Only the FOLD is serialized: leaf WORK runs in parallel (per-leaf worktrees,
   already used for producers `~:211`), and the merge+commit+plan/status mutation happens one-at-a-time on the main
   thread (gather-then-serial-fold). Parallel dispatch requires `run_leaf` support for `defer_merge=True`; a legacy
   runner that cannot defer its merge is throttled to one leaf at a time. No merge ever runs concurrently with another.

3. **Scope-contract guard** — after a leaf converges, before its merge (`controller_goal.py:~289→298`), enforce
   `changed_files ⊆ declared scope` using `git_ops.leaf_changed_files` and a new path-in-scope helper (exact file, glob,
   directory prefix; reject absolute paths and `..`). On violation: fold the leaf as a mechanical failure and emit a
   `leaf_scope_violation` event. This turns the splitter's `scope` hint (`splitter.py:~72`, validated in `frontier.py:25`)
   into an enforced contract — the foundation that makes within-wave disjointness real.

4. **Plan/spent fold on the main thread only** — the in-memory plan/status dict and the budget `spent` are mutated ONLY
   in the serial fold, never from worker threads. `stream_emit` is already cross-process flock-safe (`~:115-119`), so
   concurrent emits are fine.

## Honest boundary (the PR must state this)
Within-wave disjointness is **TEXTUAL safety only**. Semantic cross-file safety (leaf A renames a symbol leaf B calls;
A changes a unit s→ms while B passes a literal) still rests on the goal-level acceptance gate (`controller_goal.py:~906`),
which is **shadow/non-blocking unless an executable `acceptance_profile` was authored at intake** (`~:924`). Parallelism
WIDENS the window where a textually-clean-but-semantically-broken compose reaches `done`. Do NOT let the disjointness
guarantee masquerade as a correctness guarantee.

## Tests
1. path-in-scope helper unit (exact / glob / dir-prefix / absolute-reject / `..`-reject) near `test_frontier.py:~68`.
2. parallel batch (2 independent leaves, `AI_ORG_MAX_PARALLEL=2`, blocking stub, assert both START before either
   completes) near `test_controller_goal.py:~38`.
3. dependency wave (diamond A→B,C→D from `test_frontier.py:~48`): B,C run only after A folded; D only after B,C folded;
   D's worktree HEAD contains A,B,C's commits.
4. merge serialization (temp git repo; instrument `merge_and_commit_leaf` to assert concurrent entry count never > 1
   while leaf work overlaps).
5. scope guard (leaf scope `["allowed.py"]` changes `oops.py` → `oops.py` not merged, `leaf_scope_violation` emitted,
   leaf folded as mechanical failure).
6. crash isolation (one future raises / `reason="crash"`, sibling converges; exception doesn't escape; no unguarded
   merge from the crashed leaf; plan mutated only in the fold).
7. budget bound under concurrency (`budget=1`, two ready → exactly one dispatched, `budget_exhausted`; `spent`
   reconciled).
8. **serial THROTTLE — `AI_ORG_MAX_PARALLEL=1` runs one leaf at a time as a conservative throttle; the ENTIRE
   existing `test_controller_goal.py` suite stays green under `=1` and unset.** The safety net. It is a THROTTLE,
   not a byte-for-byte replay of the old serial loop: (a) a systemic leaf CRASH fail-fast-ABORTS the goal under
   `=1` exactly as the old serial path did (a crash is systemic, not a recoverable per-leaf failure — never
   retried/re-split); (b) one known, budget-bounded DRAIN-GRANULARITY difference remains — after a re-split the
   wave loop dives DEPTH-FIRST into the new children before draining the rest of an older breadth, whereas the
   old loop drained strictly breadth-first. Both terminate at the same floor under the same budget, so the
   difference changes leaf ORDER within the budget, never the set of work done or the termination guarantee.
9. legacy run_leaf compatibility: a wrapper without `defer_merge` support forces effective max workers to 1, so old
   runners that merge internally never do so concurrently.
10. empty-scope scheduling: an empty/absent-scope leaf is a repo-wide conflict and is never returned in the same ready
   batch as another leaf.

Keep green: test_controller_goal, test_frontier, test_controller_parallel, write-role-isolation, test_controller_pipeline,
test_conformance. residue clean.

## PR boundary
- **PR1 (this):** wave executor (`:1048-1195`) + merge-as-serial-fold + scope-contract guard (`:289→298`) + main-thread
  plan/spent fold + `AI_ORG_MAX_PARALLEL` reuse + `=1` serial-equivalence + tests 1-8.
- **PR2:** read-set/write-set split; cross-batch optimistic merge/rebase; dependent worktrees cut from a continuously-
  advancing head.
- **PR3 (separate track):** the OCC generation/contract fence — `controller_attempts.py` / `docs/leaf-attempts-pr1.md`.
