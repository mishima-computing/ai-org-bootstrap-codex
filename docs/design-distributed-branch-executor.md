# Distributed Branch Integration Executor

## Premise

This executor is Git-premised by design. Git is purpose-built and battle-tested for distributed parallel
work and later integration, so the executor uses Git's primitives directly instead of rebuilding their
locking, retry, object-storage, or merge behavior in Python.

The executor's job is the residual glue around Git: choose immutable bases, dispatch task work into isolated
branches/worktrees, keep application state aligned with refs, integrate in the declared dependency order, run
semantic verification over the integrated result, and clean up idempotently.

## Scope

This design changes only the `TaskExecutor` execution and integration core. It keeps `controller_goal` as the
entry point and keeps per-leaf work delegated to `controller_pipeline.run_pipeline` through the existing leaf
adapter. It does not add transform tooling, a new gate model, non-Codex carrier paths, or an in-process DAG
worker scheduler.

## A/B Boundary

| A. Delegated to Git | B. Residual executor responsibility |
| --- | --- |
| **Distinct per-task refs:** every task output is recorded under its own `refs/heads/ai-org/tasks/<parent>/<task>-<uuid>` ref. Distinct-ref updates are Git's native model and do not need executor-level contention locks. | **Application-level state must not desync from Git:** the executor must not key child results in a way that collapses distinct task refs, must reject duplicate sibling IDs, and must delete refs for failed or aborted branch tasks. |
| **Atomic ref update CAS:** task branch publication uses `git update-ref <ref> <new> <old>`. Git owns the compare-and-swap semantics; a moved ref fails the update instead of being silently overwritten. | **Correct use of Git tools:** the executor must call Git at the right time and with the right target. Cleanup removes only the specific executor-owned worktree; it must not prune or remove live sibling worktrees. |
| **Content-addressed object store:** commits, trees, and blobs are immutable objects. Concurrent loose-object writes are safe in Git's storage model and do not need Python locks or retry loops. | **Immutable base validation:** task bases must be full commit SHAs that name existing commits. Mutable refs such as branch names are rejected because a moving base is an executor planning bug, not a Git isolation feature. |
| **3-way cherry-pick/merge:** integration applies child net-diff commits with Git's merge machinery. Textual conflicts and non-zero cherry-picks are Git's job to detect. | **Semantic conflicts:** Git can merge two independently correct commits that are wrong together. Only the composite verifier or integration tests can catch that class, so verification runs after cherry-pick integration and fails closed on anything except `verified: true`. |
| **Worktree isolation:** each leaf or integration job runs in its own worktree off an immutable commit. Git owns checkout/index/worktree separation for those paths. | **Dependency ordering and base selection:** the executor computes dependency waves, topological integration order, and the base for each child. Cycles fail closed before dispatch. A child with multiple dependencies resumes from a Git-integrated dependency head. |
| **Lock files internal to Git:** Git commands use their own lock files for refs/index/worktree metadata. The executor should not add outer locks for Git-handled distinct-ref updates or object writes. | **Input validation and idempotent cleanup:** malformed decomposition output, duplicate IDs, invalid bases, failed child tasks, failed verification, and cleanup after partial setup are executor-owned failure modes. Cleanup is fail-soft but must not hide the original error. |

## Model

The executor uses Git as the parallelism boundary:

- One task runs against one isolated Git worktree state.
- A task's output is a commit recorded on that task's own branch ref.
- The controller owns integration: it creates an integration worktree from the parent base, cherry-picks task
  outputs in topological order, runs the composite verifier on the integrated head, and creates the composite's
  single net-diff commit.
- Parallel tasks share no mutable task state. The controller waits for isolated jobs to finish, then accepts
  returned branch results and updates controller-owned maps serially.

This is intentionally thinner than a general DAG scheduler. The planner only decides immutable bases,
parallel waves, and integration order; Git and process execution provide the isolation.

## Components

`TaskNode`
: Existing recursive task description. `base_sha` is the immutable commit SHA selected for this node.

`VerifiedCommit`
: Existing handoff object. Every leaf and composite still returns one verified commit.

`PlannedBranchTask`
: Controller-owned plan record for a child task. It contains `task_id`, `branch_base`, `branch_name`, and
  `depends_on`.

`BranchTaskResult`
: Completed isolated task output. It contains the plan, the returned `VerifiedCommit`, and child-local trace
  data for observability.

`TaskExecutor._execute_children`
: Thin dependency planner and dispatcher. It validates sibling IDs, builds dependency waves, plans each task
  branch, runs independent jobs concurrently, accepts returned branch commits, and deletes failed/aborted task
  refs.

`TaskExecutor._default_integrate`
: Controller integration step. It creates an `ai-org/integration/...` branch worktree from the parent base and
  cherry-picks child commits in topological order.

## Dependency Planning

Dependencies are sibling-local:

- A task with no satisfied sibling dependency branches from its declared immutable `base_sha`, or from the
  parent base.
- A task with one satisfied sibling dependency branches from that dependency's output commit.
- A task with multiple satisfied sibling dependencies branches from a controller-created integrated head that
  cherry-picks those dependency outputs together first.

`_dependency_waves` groups tasks whose sibling dependencies are already satisfied. Each wave may run in
parallel. `_topo_order` gives the controller's deterministic integration order.

Cycles fail closed. The executor raises before dispatch instead of emitting a cyclic tail as runnable work.
Duplicate sibling task IDs also fail closed because task IDs are application-level identities even though Git
branch refs are distinct.

## Integration

For a composite node:

1. Resolve and validate the parent base as an immutable commit SHA.
2. Execute children as branch jobs by dependency wave.
3. Record every successful child output commit on its distinct `refs/heads/ai-org/tasks/...` ref with
   `git update-ref` CAS.
4. Delete task refs for failed or aborted branch tasks.
5. Create an `ai-org/integration/...` worktree from the parent base.
6. Cherry-pick child commits in topological order.
7. Run the composite verifier on the integrated head.
8. If verified, create one composite integration commit off the parent base.

The parent of this composite sees only that final `VerifiedCommit`, so recursion remains commit-per-node.

## Failure Modes

Textual merge conflicts
: Detected by `git cherry-pick` returning non-zero on the integration branch. The executor aborts the
  cherry-pick, removes that integration worktree, and raises `TaskExecutorIntegrationError` with the task id,
  branch, child id, and commit prefix.

Cherry-pick failures
: Handled the same as textual conflicts. Any non-zero cherry-pick is a failed integration and is not silently
  skipped.

Non-fast-forward task branch update
: After an isolated task returns, the controller updates the planned task branch from its planned base to the
  returned commit using `git update-ref <ref> <commit> <base>`. If the ref moved, Git rejects the update and
  the executor raises `TaskExecutorIntegrationError`.

Duplicate sibling task IDs
: Rejected before dispatch. Distinct Git refs remain safe, but the executor's application state cannot safely
  represent two sibling results with the same task ID.

Mutable or invalid base
: Rejected before branch execution. A `base_sha` must be a full commit SHA that names an existing commit, not
  `main`, `HEAD`, a tag, or an abbreviated ref.

Dependency cycles
: Rejected during planning. Cyclic sibling dependencies do not become a runnable wave and do not reach
  integration.

Semantic conflicts
: Git may merge two independently correct commits that are wrong together. The composite verifier runs after
  cherry-pick integration on the integrated head. A verifier result without `verified: true` raises
  `TaskExecutorIntegrationError` before any composite commit is created.

Cleanup
: Worktree cleanup is specific-path and idempotent. It does not run `git worktree prune` while sibling tasks
  from the same wave may still be live. Failed or aborted task branch refs are deleted explicitly.

## Deadlock Elimination

The old failure class came from in-process shared executor state guarded by a lock while waiting on futures.
The distributed-branch model removes that pattern:

- Parallel jobs do not mutate the parent's results map.
- Each child job runs through its own executor instance and returns a `BranchTaskResult`.
- The parent controller mutates result maps, trace lists, and integration state only after `future.result()`
  returns.
- `TaskExecutor` has no shared trace/resource lock held across any blocking future wait.

The remaining synchronization is the `concurrent.futures` wait used to collect isolated branch-job results.
Git refs and worktrees are the task boundary; controller-owned integration is serial and explicit.
