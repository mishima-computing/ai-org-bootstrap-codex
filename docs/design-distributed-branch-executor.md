# Distributed Branch Integration Executor

## Scope

This design changes only the `TaskExecutor` execution and integration core. It keeps `controller_goal` as the
entry point and keeps per-leaf work delegated to `controller_pipeline.run_pipeline` through the existing leaf
adapter. It does not add transform tooling, a new gate model, or an in-process DAG worker scheduler.

## Model

The executor uses Git as the parallelism boundary:

- One task runs against one isolated git worktree state.
- A task's output is a commit recorded on that task's own branch.
- The controller owns integration: it creates an integration branch from the parent base, cherry-picks task
  branch outputs in planned order, runs the composite verifier on the integrated head, and then creates the
  composite's single net-diff commit.
- Parallel tasks share no mutable task state. The controller waits for isolated jobs to finish, then records
  their returned commits.

This is intentionally thinner than a general DAG scheduler. The planner only decides branch bases, parallel
waves, and integration order; Git and process execution do the isolation.

## Components

`TaskNode`
: Existing recursive task description. `base_sha` is the branch base chosen by the controller for this node.

`VerifiedCommit`
: Existing handoff object. Every leaf and composite still returns one verified commit.

`PlannedBranchTask`
: Controller-owned plan record for a child task. It contains `task_id`, `branch_base`, `branch_name`, and
  `depends_on`.

`BranchTaskResult`
: Completed isolated task output. It contains the plan, the returned `VerifiedCommit`, and child-local trace
  data for observability.

`TaskExecutor._execute_children`
: Thin dependency planner and dispatcher. It builds dependency waves, plans each task branch, runs independent
  jobs concurrently, and accepts returned branch commits.

`TaskExecutor._default_integrate`
: Controller integration step. It creates an `ai-org/integration/...` branch worktree from the parent base and
  cherry-picks child commits in topological order.

## Dependency Planning

Dependencies are sibling-local:

- A task with no satisfied sibling dependency branches from its declared `base_sha`, or from the parent base.
- A task with one satisfied sibling dependency branches from that dependency's output commit.
- A task with multiple satisfied sibling dependencies branches from a controller-created integrated head that
  cherry-picks those dependency outputs together first.

`_dependency_waves` groups tasks whose sibling dependencies are already satisfied. Each wave may run in
parallel. `_topo_order` gives the controller's deterministic integration order.

Cycles are not silently dropped. The existing wave helper emits the unresolved tail as a final wave, so any
bad ordering is forced through the same branch execution and integration path, where failures surface loudly.

## Interfaces

Leaf execution stays injectable and compatible:

```python
TaskExecutor(repo, run_leaf=..., verify=..., integrate=..., commit_integration=...)
```

The default leaf path still creates a leaf worktree, calls `controller_pipeline.run_pipeline`, and captures a
commit off the planned base. Injected tests can still return `VerifiedCommit`, a dict with `commit_sha` or
`commit`, or a bare SHA.

The new internal branch interface is:

```python
PlannedBranchTask(task_id, branch_base, branch_name, depends_on)
BranchTaskResult(plan, verified, calls, recursion_edges)
```

These are controller/executor internals, not a new external API.

## Integration

For a composite node:

1. Resolve the parent base.
2. Execute children as branch jobs by dependency wave.
3. Record every child output commit on `refs/heads/ai-org/tasks/...`.
4. Create an `ai-org/integration/...` worktree from the parent base.
5. Cherry-pick child commits in topological order.
6. Run the composite verifier on the integrated head.
7. If verified, create one composite integration commit off the parent base.

The parent of this composite sees only that final `VerifiedCommit`, so recursion remains commit-per-node.

## Failure Modes

Textual merge conflicts
: Detected by `git cherry-pick` returning non-zero on the integration branch. The executor aborts the
  cherry-pick, cleans the integration worktree, and raises `TaskExecutorIntegrationError` with the task id,
  branch, child id, and commit prefix.

Cherry-pick failures
: Handled the same as textual conflicts. Any non-zero cherry-pick is a failed integration and is not silently
  skipped.

Non-fast-forward task branch update
: After an isolated task returns, the controller updates the planned task branch from its planned base to the
  returned commit using `git update-ref <ref> <commit> <base>`. If the ref moved, update-ref fails and the
  executor raises `TaskExecutorIntegrationError`.

Integration order
: `_topo_order` is the single order used when applying child outputs to the integration branch. Dependency
  waves affect only dispatch parallelism, not final integration order.

Semantic conflicts
: Git may merge two independently correct commits that are wrong together. The composite verifier runs after
  cherry-pick integration on the integrated head. A verifier result without `verified: true` raises
  `TaskExecutorIntegrationError` before any composite commit is created.

## Deadlock Elimination

The old failure class came from in-process shared executor state guarded by a lock while waiting on futures.
The distributed-branch model removes that pattern:

- Parallel jobs do not mutate the parent's results map.
- Each child job runs through its own executor instance and returns a `BranchTaskResult`.
- The parent controller mutates `results`, trace lists, and integration state only after `future.result()`
  returns.
- `TaskExecutor` no longer has shared trace/resource locks, and no lock is held across any blocking future wait.

The only synchronization left is the `concurrent.futures` wait used to collect isolated branch-job results.
Git refs and worktrees are the task boundary; controller-owned integration is serial and explicit.
