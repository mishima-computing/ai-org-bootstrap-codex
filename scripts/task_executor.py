#!/usr/bin/env python3
"""TaskGraph / TaskNode / TaskExecutor — the recursive task-graph executer (owner-confirmed model).

The model in one line: a ``TaskGraph`` is a tree of ``TaskNode``s, and ``TaskExecutor.execute(node)`` returns a
``VerifiedCommit`` for EVERY node. The recursion is TRUE recursion — ``execute`` calls ``execute`` on a
composite's children, and every node (leaf OR internal) returns a verified commit:

  * a LEAF executes via the ``controller_pipeline`` dialectic (aufheben designer + implementer + Linon),
    producing a single, cherry-pickable commit off the node's ``base_sha``;
  * a COMPOSITE executes its children RECURSIVELY (``execute`` -> ``execute``, with PR1-style bounded
    parallelism for children whose ``depends_on`` allows it), then INTEGRATES their verified commits in
    topological order on an integration branch off the node's ``base_sha``, VERIFIES the integrated
    result (Linon / acceptance), and creates its OWN integration commit.

So the recursion closes: an internal node is not a mere router — it integrates + verifies + commits,
exactly like a leaf returns a commit. Every node yields a ``VerifiedCommit``.

Reuses the existing assets rather than re-deriving them:
  * ``controller_pipeline.run_pipeline`` — the leaf dialectic (designer + implementer + Linon);
  * ``git_ops`` — the per-commit git guards (identity, literal pathspecs, scratch exclusion);
  * the PR1 within-batch parallelism shape (``concurrent.futures`` over independent siblings).

The per-leaf runner (``run_leaf``) and the composite verifier (``verify``) are INJECTABLE — exactly as
``controller_goal`` injects ``run_leaf`` — so the recursion can be tested without a carrier.
``controller_goal.run_goal`` is left in place to run in parallel; this module is the new model.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import git_ops  # noqa: E402
from splitter import HOUSE_RULES  # noqa: E402

LEAF = "leaf"
COMPOSITE = "composite"
FLOOR_MAX_DEPTH = 3
LLM_MIN_DEPTH = 1
LLM_MAX_DEPTH = 5

_DECOMPOSE_TASK_KEYS = {"id", "objective", "depends_on", "base_sha"}


class TaskExecutorIntegrationError(RuntimeError):
    """An internal node could not integrate its children's commits (a cherry-pick conflict, a failed
    commit-tree, or a missing base). The recursion cannot close, so the node fails loudly."""


# --------------------------------------------------------------------------------------------------
# The model: TaskNode / TaskGraph / VerifiedCommit
# --------------------------------------------------------------------------------------------------
@dataclass
class TaskNode:
    """One node of the task tree. A ``leaf`` is the smallest unit (built by the dialectic); a
    ``composite`` owns ``subtasks`` and is executed by recursing into them and integrating the results.

    ``base_sha`` is the commit the node's work is cut from — a leaf's commit and a composite's
    integration are both made relative to it, so any node's commit is a self-contained, cherry-pickable
    net diff that its parent can integrate. ``objective`` is the node's spec (the work to do).
    """
    id: str
    kind: str = LEAF
    subtasks: list["TaskNode"] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    base_sha: str | None = None
    objective: str = ""
    depth: int = 0

    @property
    def spec(self) -> str:
        """Alias for ``objective`` — the node's spec/objective text are the same field."""
        return self.objective

    @property
    def is_leaf(self) -> bool:
        return self.kind == LEAF and not self.subtasks


@dataclass
class VerifiedCommit:
    """What EVERY node returns: the commit that carries the node's verified work, plus the evidence
    that verified it. For a leaf, ``commit_sha`` is the leaf's dialectic commit; for a composite, it is
    the node's integration commit. ``evidence`` records what verified the commit (Linon findings count,
    integrated children, the verifier's report)."""
    task_id: str
    commit_sha: str
    evidence: dict = field(default_factory=dict)


@dataclass
class TaskGraph:
    """The root ``TaskNode`` plus tree helpers."""
    root: TaskNode

    def nodes(self):
        """Yield every node in the tree, pre-order (root first)."""
        def walk(node: TaskNode):
            yield node
            for child in node.subtasks:
                yield from walk(child)
        yield from walk(self.root)

    def leaves(self) -> list[TaskNode]:
        """Every leaf node (no subtasks)."""
        return [node for node in self.nodes() if not node.subtasks]

    def get(self, task_id: str) -> TaskNode | None:
        """The node with this id, or None."""
        for node in self.nodes():
            if node.id == task_id:
                return node
        return None


@dataclass
class DecomposeResult:
    """Schema-gated decompose output plus optional root-selected max depth."""
    children: list[TaskNode]
    max_depth: int | None = None


# --------------------------------------------------------------------------------------------------
# Topological ordering + dependency waves over a node's siblings (depends_on within one level)
# --------------------------------------------------------------------------------------------------
def _topo_order(children: list[TaskNode]) -> list[TaskNode]:
    """Order sibling children so a child comes after every (sibling) dependency it ``depends_on``.
    Kahn's algorithm; ids outside the sibling set are ignored (a cross-level dep is not a sibling edge).
    Stable in plan order for independent children. A cycle leaves the unresolved tail appended in plan
    order rather than dropping it."""
    by_id = {child.id: child for child in children}
    indeg = {child.id: sum(1 for d in child.depends_on if d in by_id) for child in children}
    ready = [child for child in children if indeg[child.id] == 0]
    ordered: list[TaskNode] = []
    seen: set[str] = set()
    while ready:
        node = ready.pop(0)
        if node.id in seen:
            continue
        ordered.append(node)
        seen.add(node.id)
        for child in children:
            if child.id in seen:
                continue
            if node.id in child.depends_on:
                indeg[child.id] -= 1
                if indeg[child.id] == 0:
                    ready.append(child)
    if len(ordered) != len(children):                      # a cycle -> keep the tail (plan order), never drop
        ordered.extend(child for child in children if child.id not in seen)
    return ordered


def _dependency_waves(children: list[TaskNode]) -> list[list[TaskNode]]:
    """Group sibling children into successive waves where each wave's nodes have all their sibling
    ``depends_on`` satisfied by an earlier wave. Independent children share a wave and run concurrently
    (the PR1 within-batch frontier-leaf parallelism shape). A cycle is emitted as a final single wave so
    the execute never deadlocks."""
    by_id = {child.id for child in children}
    done: set[str] = set()
    remaining = list(children)
    waves: list[list[TaskNode]] = []
    while remaining:
        wave = [c for c in remaining if all(d in done or d not in by_id for d in c.depends_on)]
        if not wave:                                        # unresolved cycle -> run the rest as one wave
            wave = remaining
        waves.append(wave)
        done.update(c.id for c in wave)
        remaining = [c for c in remaining if c.id not in done]
    return waves


def _max_parallel() -> int:
    """Bounded sibling parallelism, mirroring controller_goal's AI_ORG_MAX_PARALLEL (default 4)."""
    try:
        return max(1, int(os.environ.get("AI_ORG_MAX_PARALLEL", "4")))
    except ValueError:
        return 4


def _max_depth() -> int:
    """Bounded recursion depth, defaulting to FLOOR_MAX_DEPTH."""
    try:
        return max(1, int(os.environ.get("AI_ORG_MAX_DEPTH", str(FLOOR_MAX_DEPTH))))
    except ValueError:
        return FLOOR_MAX_DEPTH


# --------------------------------------------------------------------------------------------------
# Decomposer: composite TaskNode -> child TaskNodes / sub-TaskGraphs
# --------------------------------------------------------------------------------------------------
def should_be_leaf(node: TaskNode, max_depth: int) -> bool:
    """Return true only for deterministic leaf cases.

    Python does not judge whether an objective is small enough. A no-child composite becomes a leaf only
    when it was explicitly declared as one or the recursion ceiling forces a hard stop; otherwise the
    decomposer decides by returning children or an empty array.
    """
    if node.subtasks:
        return False
    if node.kind == LEAF:
        return True
    if node.depth >= max_depth:
        return True
    return False


def _build_decompose_prompt(node: TaskNode, max_depth: int) -> str:
    root_depth_schema = (
        'At depth 0 only, return a JSON object: {"max_depth": <integer 1-5>, "children": [...]}. '
        "Choose max_depth from the goal's size/complexity: tiny goals use 1-2; huge goals use 5. "
        "At non-root depths, return only the JSON children array.\n\n"
        if node.depth == 0 else ""
    )
    return (
        "Decompose this TaskNode into child TaskNodes for the recursive TaskGraph/TaskExecutor model.\n"
        "A split means this TaskGraph hangs MULTIPLE child TaskGraphs below it. Every child is a "
        "TaskNode/TaskGraph that may decompose further.\n\n"
        "There are only TWO reasons to split:\n"
        "(1) PARALLELISM: separate genuinely INDEPENDENT work (no shared scope, no dependency between "
        "them) so children run concurrently.\n"
        "(2) REVIEWABILITY: each task must be small enough that the adversarial reviewer (Linon) can "
        "verify it COMPLETELY in one pass. Judge this by IMPACT / BLAST RADIUS — everything it touches "
        "across the system — NOT line count: a one-line edit to a shared contract is hard to verify; "
        "a large edit confined to a leaf module is easy.\n\n"
        "Make each task as LARGE as possible while still satisfying both — do NOT decompose to the "
        "smallest unit. Over-splitting inherently-sequential work pays the heavy review cost N times "
        "with no parallel gain. Start COARSE: a task that later proves too big is split further "
        "automatically by the recursion, so do not pre-split everything.\n\n"
        "SCAFFOLD / greenfield is ATOMIC: creating a project skeleton (interdependent files — manifest, "
        "entry module, config — that must all exist together) is ONE task; do NOT split it. A skeleton "
        "cannot be built one file at a time. Likewise never label a task minimal/atomic and then split "
        "it. For these, return an EMPTY children array [] == leaf.\n\n"
        "Isolate a high-impact shared-interface change into its OWN task; order tasks by depends_on.\n\n"
        f"You are at depth {node.depth} of max {max_depth}. If you are at or beyond the max depth you "
        "MUST return an empty children array (this node is a leaf).\n\n"
        "Two-stage instruction, in this exact order:\n"
        "1. PARALLEL split first: decompose the task into INDEPENDENT child tasks that can run "
        "CONCURRENTLY. This is the speed win. Independent children must have no depends_on between them.\n"
        "2. THEN, only if a parallel piece's granularity is STILL too large for the LLM to handle in one "
        "unit, SERIAL split that piece into a DEPENDENT sequential chain using depends_on edges. Accept no "
        "parallel speedup here; this fallback is only for granularity, not speed.\n\n"
        "Prefer parallel independent children. Only serial-split when a piece is too big for the LLM and "
        "cannot be parallelized. depends_on encodes serial/dependent versus parallel/independent.\n"
        "If this task is atomic / small enough to implement in one unit, return an EMPTY JSON array [] -- "
        "it is a leaf. Otherwise split it into children.\n\n"
        + root_depth_schema +
        "Return only JSON. Each child object must contain exactly id, objective, depends_on, and base_sha. "
        "id and objective are strings; depends_on is a list of strings; base_sha is a string or null.\n\n"
        f"Parent id: {node.id}\n"
        f"Parent base_sha: {node.base_sha or ''}\n"
        f"Parent objective:\n{node.objective}\n\n"
        f"HOUSE_RULES:\n{HOUSE_RULES}"
    )


def _normalize_string_list(value):
    if not isinstance(value, list):
        raise ValueError("expected list")
    if any(not isinstance(item, str) for item in value):
        raise ValueError("expected list of strings")
    return list(value)


def _tasknode_from_decompose_object(task: dict, parent: TaskNode, max_depth: int) -> TaskNode:
    if not isinstance(task, dict):
        raise ValueError("expected task object")
    if set(task) != _DECOMPOSE_TASK_KEYS:
        raise ValueError("expected exact task keys")
    if not isinstance(task["id"], str) or not task["id"]:
        raise ValueError("expected non-empty string id")
    if not isinstance(task["objective"], str) or not task["objective"]:
        raise ValueError("expected non-empty string objective")
    if task["base_sha"] is not None and not isinstance(task["base_sha"], str):
        raise ValueError("expected base_sha string or null")
    child = TaskNode(
        id=task["id"],
        kind=COMPOSITE,
        depends_on=_normalize_string_list(task["depends_on"]),
        base_sha=task["base_sha"] or parent.base_sha,
        objective=task["objective"],
        depth=parent.depth + 1,
    )
    child.kind = LEAF if should_be_leaf(child, max_depth) else COMPOSITE
    return child


def _clamped_llm_max_depth(value) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return min(LLM_MAX_DEPTH, max(LLM_MIN_DEPTH, parsed))


def decompose_with_metadata(node: TaskNode, carrier, max_depth: int) -> DecomposeResult:
    """Ask a read-only carrier to produce this node's child TaskNodes and schema-gate the handoff.

    The carrier is injectable for tests and must accept one prompt string and return JSON text. Existing
    plain child arrays stay valid. A root response may instead be an object with ``max_depth`` and
    ``children``; malformed or omitted max_depth is ignored while valid children are still accepted.
    Any malformed carrier output is rejected as no children; the executer decides how to handle an unusable
    decomposition.
    """
    try:
        prompt = _build_decompose_prompt(node, max_depth)
        raw = carrier(prompt)
        value = json.loads(raw)
        chosen_max_depth = None
        if isinstance(value, dict):
            chosen_max_depth = _clamped_llm_max_depth(value.get("max_depth"))
            value = value.get("children")
        if not isinstance(value, list):
            raise ValueError("expected task array")
        effective_max_depth = chosen_max_depth if node.depth == 0 and chosen_max_depth is not None else max_depth
        return DecomposeResult(
            children=[_tasknode_from_decompose_object(task, node, effective_max_depth) for task in value],
            max_depth=chosen_max_depth,
        )
    except Exception:
        return DecomposeResult(children=[])


def decompose(node: TaskNode, carrier, max_depth: int) -> list[TaskNode]:
    """Compatibility wrapper returning only the schema-gated child TaskNodes."""
    return decompose_with_metadata(node, carrier, max_depth).children


def codex_decompose_carrier(repo, *, model=None, resume_session=None):
    """Read-only Codex carrier adapter for decomposition, following controller_goal.codex_carrier."""
    captured: dict = {}

    def carrier(prompt):
        import carrier_harness
        out = Path(tempfile.mkdtemp(prefix="decompose-")) / "tasks.json"
        try:
            result = carrier_harness.run_carrier(repo, prompt, sandbox="read-only",
                                                 output_file=str(out), model=model, retries=1,
                                                 resume_session=resume_session)
            captured["session_id"] = result.get("session_id")
            if result.get("ok") and out.is_file():
                return out.read_text(encoding="utf-8")
        except Exception:
            pass
        finally:
            shutil.rmtree(out.parent, ignore_errors=True)
        return "[]"

    carrier.captured = captured
    return carrier


# --------------------------------------------------------------------------------------------------
# The recursive driver
# --------------------------------------------------------------------------------------------------
class TaskExecutor:
    """The recursive task_executor. ``execute(node)`` dispatches a leaf to ``execute_leaf`` and a composite to
    ``execute_composite``; ``execute_composite`` calls ``execute`` on each child — that self-call IS the true
    recursion. Both ``run_leaf`` (the leaf dialectic) and ``verify`` (the composite acceptance) are
    injectable for testing; the git integration is the executer's own machinery (an internal node MUST
    integrate + verify + commit)."""

    def __init__(self, repo=None, *, run_leaf=None, verify=None, integrate=None, commit_integration=None,
                 decompose_carrier=None, decomposer=None, max_parallel: int | None = None,
                 max_depth: int | None = None, emit=None):
        self.repo = Path(repo) if repo else None
        self._run_leaf = run_leaf or self._default_run_leaf
        self._verify = verify or self._default_verify
        self._integrate = integrate or self._default_integrate
        self._commit_integration = commit_integration or self._default_commit_integration
        self._decompose_carrier = decompose_carrier
        self._decomposer = decomposer or self._default_decompose
        self.max_parallel = max_parallel if max_parallel is not None else _max_parallel()
        self.max_depth = max_depth if max_depth is not None else _max_depth()
        self._emit = emit or (lambda _event: None)
        # recursion trace (proves TaskExecutor -> TaskExecutor): every execute() call's node id, and the parent->child
        # edges followed when a composite recursed into its children.
        self.calls: list[str] = []
        self.recursion_edges: list[tuple[str, str]] = []
        self._lock = threading.Lock()
        self._abort = threading.Event()
        self._active_worktrees: set[Path] = set()
        self._active_pgids: set[int] = set()

    # -- dispatch ---------------------------------------------------------------------------------
    def execute(self, node: TaskNode) -> VerifiedCommit:
        """Execute ANY node and return its VerifiedCommit. Leaf -> dialectic; composite -> recurse +
        integrate + verify + commit."""
        with self._lock:
            self.calls.append(node.id)
        self._emit({"type": "leaf_start", "id": node.id, "kind": node.kind, "depth": node.depth})
        if node.subtasks:
            return self.execute_composite(node)
        if should_be_leaf(node, self.max_depth):
            return self.execute_leaf(node)
        node.subtasks = self._decompose_node(node)
        if node.subtasks:
            self._emit({"type": "leaf_split", "id": node.id, "n": len(node.subtasks),
                        "children": [child.id for child in node.subtasks]})
            return self.execute_composite(node)
        self._emit({"type": "decompose_empty_fallback", "id": node.id, "kind": node.kind,
                    "depth": node.depth,
                    "detail": "decomposer returned no children; executing the node as a single leaf"})
        return self.execute_leaf(node)

    def execute_leaf(self, node: TaskNode) -> VerifiedCommit:
        """A leaf: run the dialectic (injected ``run_leaf``, default = controller_pipeline) and return
        its verified commit."""
        result = self._run_leaf(node)
        verified = _as_verified_commit(result, node)
        self._emit({"type": "leaf_done", "id": node.id, "commit": verified.commit_sha})
        return verified

    def execute_composite(self, node: TaskNode) -> VerifiedCommit:
        """A composite: execute children RECURSIVELY (parallel where depends_on allows), integrate their
        commits in topological order on an integration branch off ``base_sha``, verify the integrated
        result, and create THIS node's integration commit — the whole point of the recursion."""
        base = node.base_sha if node.base_sha is not None else self._current_head()
        if not node.subtasks:
            node.subtasks = self._decompose_node(node)
            if not node.subtasks:
                self._emit({"type": "decompose_empty_fallback", "id": node.id, "kind": node.kind,
                            "depth": node.depth,
                            "detail": "composite decomposed to no children; executing it as a single leaf"})
                return self.execute_leaf(node)
            self._emit({"type": "leaf_split", "id": node.id, "n": len(node.subtasks),
                        "children": [child.id for child in node.subtasks]})
        # 1. execute children recursively (this is where execute calls execute)
        child_commits = self._execute_children(node, base)
        # 2. integrate the children's commits in TOPOLOGICAL order on a branch off base
        ordered = _topo_order(node.subtasks)
        ordered_commits = [child_commits[c.id] for c in ordered if c.id in child_commits]
        integ_wt = None
        try:
            integrated_head, integ_wt = self._integrate(node, base, ordered_commits)
            # 3. verify the INTEGRATED result (Linon / acceptance over the integrated commit)
            evidence = self._verify(node, integrated_head, ordered_commits)
            if not _verification_confirmed(evidence):
                raise TaskExecutorIntegrationError(
                    f"composite verification failed for {node.id}: {evidence!r}")
            # 4. create THIS node's integration commit (a single, cherry-pickable net diff off base)
            integration_sha = self._commit_integration(node, base, integrated_head, integ_wt, evidence)
        finally:
            self._cleanup_worktree(integ_wt)
        return VerifiedCommit(
            task_id=node.id,
            commit_sha=integration_sha,
            evidence={
                "kind": "integration",
                "integrated_children": [c.commit_sha for c in ordered_commits],
                "verify": evidence,
            },
        )

    def _execute_children(self, node: TaskNode, base: str) -> dict[str, VerifiedCommit]:
        """Recurse into each child, THREADING the commit along ``depends_on`` edges.

        Per the owner-confirmed model, a child's base is decided by its SATISFIED sibling dependencies
        (the deps already executed in an earlier wave, present in ``results``):

          * ZERO sibling deps -> the parent ``base`` (the INDEPENDENT/PARALLEL case — no inheritance,
            unchanged; a child that already declares its own ``base_sha`` keeps it);
          * ONE sibling dep    -> that dependency's OUTPUT commit, so the child RESUMES from it (serial);
          * MULTIPLE sibling deps -> their output commits INTEGRATED first (reusing ``_integrate``), so the
            child builds on the combined work.

        Independent children share a dependency wave and run concurrently, bounded by ``max_parallel``
        (the PR1 within-batch shape). The base is computed BEFORE dispatch, so the parallel path is
        unchanged for independent children (each still cuts from the parent base)."""
        results: dict[str, VerifiedCommit] = {}
        for wave in _dependency_waves(node.subtasks):
            for child in wave:                              # thread the commit: pick each child's base
                child.base_sha = self._child_base(node, base, child, results)
            if self.max_parallel <= 1 or len(wave) == 1:
                for child in wave:
                    self._record_edge(node, child)
                    results[child.id] = self.execute(child)
            else:
                executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=min(self.max_parallel, len(wave)))
                futures = {}
                try:
                    for child in wave:
                        self._record_edge(node, child)
                        futures[executor.submit(self.execute, child)] = child
                    for future in concurrent.futures.as_completed(futures):
                        child = futures[future]
                        try:
                            results[child.id] = future.result()
                        except BaseException:
                            self._abort_wave(futures)
                            raise
                except BaseException:
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise
                else:
                    executor.shutdown(wait=True)
        return results

    def _child_base(self, node: TaskNode, base: str, child: TaskNode,
                    results: dict[str, VerifiedCommit]) -> str:
        """Reexecute the base a child cuts its work from, threading ``depends_on`` (see ``_execute_children``).

        With no satisfied sibling dep the child is independent: keep its own declared ``base_sha`` if it
        has one, else the parent ``base``. With exactly one sibling dep it RESUMES from that dep's output
        commit. With several it resumes from those deps' commits integrated together (``_integrate``), so
        the intermediate integration worktree is created and removed here — only its head sha is kept."""
        dep_commits = [results[d] for d in child.depends_on if d in results]
        if not dep_commits:                                 # independent / parallel: no inheritance
            return child.base_sha if child.base_sha is not None else base
        if len(dep_commits) == 1:                           # serial: resume from the dep's output commit
            return dep_commits[0].commit_sha
        integ_wt = None
        try:
            integrated_head, integ_wt = self._integrate(node, base, dep_commits)  # resume from integrated deps
            return integrated_head
        finally:
            self._cleanup_worktree(integ_wt)

    def _record_edge(self, node: TaskNode, child: TaskNode) -> None:
        with self._lock:
            self.recursion_edges.append((node.id, child.id))

    def _decompose_node(self, node: TaskNode) -> list[TaskNode]:
        result = self._decomposer(node)
        if isinstance(result, DecomposeResult):
            if node.depth == 0 and result.max_depth is not None:
                self.max_depth = result.max_depth
            return result.children
        if isinstance(result, list):
            return result
        return []

    def _default_decompose(self, node: TaskNode) -> DecomposeResult:
        carrier = self._decompose_carrier
        if carrier is None:
            if self.repo is None:
                return DecomposeResult(children=[])
            carrier = codex_decompose_carrier(self.repo)
            self._decompose_carrier = carrier
        return decompose_with_metadata(node, carrier, self.max_depth)

    # -- default git machinery (overridable for tests) --------------------------------------------
    def _register_worktree(self, wt) -> None:
        if wt:
            with self._lock:
                self._active_worktrees.add(Path(wt))

    def _cleanup_worktree(self, wt) -> None:
        """Idempotently remove an executor-owned git worktree and prune stale metadata.

        Cleanup is fail-soft by design: callers use it from exception/finally paths, so an already-removed
        worktree, a stale git metadata entry, or a partially-created tempdir must never mask the real error.
        """
        if not wt:
            return
        path = Path(wt)
        try:
            if self.repo is not None:
                self._git("worktree", "remove", "--force", str(path))
                self._git("worktree", "prune")
        except Exception:  # noqa: BLE001 - cleanup must be idempotent/fail-soft
            pass
        shutil.rmtree(path, ignore_errors=True)
        with self._lock:
            self._active_worktrees.discard(path)

    def register_carrier_pgid(self, pgid: int | None) -> None:
        """Allow leaf/integration adapters that spawn carriers to give abort cleanup their process group."""
        if pgid is None:
            return
        try:
            pgid = int(pgid)
        except (TypeError, ValueError):
            return
        if pgid <= 0:
            return
        with self._lock:
            self._active_pgids.add(pgid)

    def unregister_carrier_pgid(self, pgid: int | None) -> None:
        try:
            pgid = int(pgid)
        except (TypeError, ValueError):
            return
        with self._lock:
            self._active_pgids.discard(pgid)

    def _kill_carrier_pgid(self, pgid: int) -> None:
        try:
            import carrier_harness
            carrier_harness._kill_process_group(pgid)
            return
        except Exception:  # noqa: BLE001 - fall back to the same killpg pattern below
            pass
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError, AttributeError):
            pass

    def _cleanup_active_resources(self) -> None:
        with self._lock:
            pgids = list(self._active_pgids)
            worktrees = list(self._active_worktrees)
        for pgid in pgids:
            self._kill_carrier_pgid(pgid)
            self.unregister_carrier_pgid(pgid)
        for wt in worktrees:
            self._cleanup_worktree(wt)

    def _abort_wave(self, futures: dict[concurrent.futures.Future, TaskNode]) -> None:
        self._abort.set()
        for future in futures:
            future.cancel()
        self._cleanup_active_resources()

    def _git(self, *args, cwd=None) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(cwd or self.repo), *args], capture_output=True, text=True)

    def _current_head(self) -> str:
        if self.repo is None:
            raise TaskExecutorIntegrationError(
                "TaskExecutor.repo is required to resolve a node's base_sha (set node.base_sha, or inject "
                "run_leaf/integrate for a repo-less run)")
        head = self._git("rev-parse", "HEAD").stdout.strip()
        if not head:
            raise TaskExecutorIntegrationError("could not resolve repo HEAD")
        return head

    def _commit_tree_off_base(self, wt: Path, base: str, message: str) -> str:
        """Capture a worktree's deliverable changes (scratch excluded, literal pathspecs) as ONE commit
        whose parent is ``base`` — a self-contained net diff the parent can cherry-pick. Reuses git_ops'
        identity + scratch guards. Returns the new commit sha."""
        git_ops.ensure_identity(self.repo)
        files = git_ops.leaf_changed_files(wt)
        if files:
            specs = [f":(literal){r}" for r in files]
            self._git("add", "--", *specs, cwd=wt)
        tree = self._git("write-tree", cwd=wt).stdout.strip()
        if not tree:
            raise TaskExecutorIntegrationError(f"write-tree failed in {wt}")
        out = self._git("commit-tree", tree, "-p", base, "-m", message)
        sha = out.stdout.strip()
        if not sha:
            raise TaskExecutorIntegrationError(f"commit-tree failed: {out.stderr.strip()}")
        return sha

    def _default_run_leaf(self, node: TaskNode) -> VerifiedCommit:
        """The real leaf adapter: run the controller_pipeline dialectic in a fresh worktree off the
        leaf's base_sha, then capture the leaf's net change as ONE cherry-pickable commit off that base.
        Mirrors controller_goal.default_run_leaf's worktree isolation."""
        import controller_pipeline
        base = node.base_sha or self._current_head()
        wt = Path(tempfile.mkdtemp(prefix=f"executer-leaf-{node.id}-"))
        add = self._git("worktree", "add", "--detach", str(wt), base)
        if add.returncode != 0:
            shutil.rmtree(wt, ignore_errors=True)
            raise TaskExecutorIntegrationError(f"worktree add failed for leaf {node.id}: {add.stderr.strip()}")
        self._register_worktree(wt)
        carrier_harness = None
        pgid_observer = None
        try:
            try:
                import carrier_harness as _carrier_harness
                carrier_harness = _carrier_harness
                pgid_observer = carrier_harness.register_process_group_observer(
                    self.register_carrier_pgid, self.unregister_carrier_pgid)
            except Exception:  # noqa: BLE001 - worktree cleanup still protects the leaf if observing is unavailable
                carrier_harness = None
                pgid_observer = None
            run_id = "execute-" + uuid.uuid4().hex[:10]
            result = controller_pipeline.run_pipeline(wt, node.objective, run_id,
                                                      max_parallel=self.max_parallel)
            sha = self._commit_tree_off_base(wt, base, f"leaf: {node.id}")
            evidence = {
                "kind": "leaf",
                "converged": bool(result.get("converged")),
                "linon_findings_count": result.get("linon_findings_count"),
            }
            return VerifiedCommit(node.id, sha, evidence)
        finally:
            if carrier_harness is not None:
                carrier_harness.unregister_process_group_observer(pgid_observer)
            self._cleanup_worktree(wt)

    def _default_integrate(self, node: TaskNode, base: str,
                           child_commits: list[VerifiedCommit]) -> tuple[str, str]:
        """Make an integration branch (detached worktree) off ``base`` and apply each child's net-diff
        commit in topological order via cherry-pick. Returns (integrated_head_sha, worktree_path); the
        worktree is owned by the caller (consumed by _commit_integration)."""
        wt = Path(tempfile.mkdtemp(prefix=f"executer-integ-{node.id}-"))
        add = self._git("worktree", "add", "--detach", str(wt), base)
        if add.returncode != 0:
            shutil.rmtree(wt, ignore_errors=True)
            raise TaskExecutorIntegrationError(
                f"integration worktree add failed for {node.id}: {add.stderr.strip()}")
        self._register_worktree(wt)
        handed_off = False
        try:
            git_ops.ensure_identity(self.repo)
            for cc in child_commits:
                cp = self._git("cherry-pick", "--allow-empty", "--keep-redundant-commits",
                               cc.commit_sha, cwd=wt)
                if cp.returncode != 0:
                    self._git("cherry-pick", "--abort", cwd=wt)
                    raise TaskExecutorIntegrationError(
                        f"integration conflict in {node.id} applying {cc.task_id} "
                        f"({cc.commit_sha[:8]}): {cp.stderr.strip()}")
            head = self._git("rev-parse", "HEAD", cwd=wt).stdout.strip()
            handed_off = True
            return head, str(wt)
        finally:
            if not handed_off:
                self._cleanup_worktree(wt)

    def _default_commit_integration(self, node: TaskNode, base: str, integrated_head: str,
                                    integ_wt, evidence: dict) -> str:
        """Squash the integrated worktree's tree into ONE integration commit off ``base`` — so this
        composite's whole subtree is a single, cherry-pickable net diff for its own parent. Removes the
        integration worktree. Returns the integration commit sha."""
        try:
            tree = self._git("rev-parse", f"{integrated_head}^{{tree}}", cwd=integ_wt).stdout.strip()
            if not tree:
                raise TaskExecutorIntegrationError(f"could not resolve integrated tree for {node.id}")
            git_ops.ensure_identity(self.repo)
            out = self._git("commit-tree", tree, "-p", base, "-m", f"integrate: {node.id}")
            sha = out.stdout.strip()
            if not sha:
                raise TaskExecutorIntegrationError(f"integration commit-tree failed for {node.id}: "
                                             f"{out.stderr.strip()}")
            return sha
        finally:
            self._cleanup_worktree(integ_wt)

    def _default_verify(self, node: TaskNode, integrated_head: str,
                        child_commits: list[VerifiedCommit]) -> dict:
        """The composite acceptance gate over the INTEGRATED result. The expensive per-leaf semantic
        review (Linon) already ran inside each leaf's dialectic; the composite's default check confirms
        the children integrated cleanly (the cherry-picks applied without conflict, an integrated head
        exists). Real Linon/acceptance re-run over the integrated tree is injected via ``verify``."""
        return {
            "method": "integration-clean",
            "verified": bool(integrated_head),
            "integrated_head": integrated_head,
            "children": [c.task_id for c in child_commits],
        }


def _verification_confirmed(evidence) -> bool:
    """Composite verification is fail-closed: only an explicit verified:true is green."""
    return isinstance(evidence, dict) and evidence.get("verified") is True


def _as_verified_commit(result, node: TaskNode) -> VerifiedCommit:
    """Adapt an injected run_leaf's return into a VerifiedCommit. Accepts a VerifiedCommit (returned
    as-is), a dict ({commit_sha|commit, evidence}), or a bare sha string."""
    if isinstance(result, VerifiedCommit):
        return result
    if isinstance(result, str):
        return VerifiedCommit(node.id, result, {})
    if isinstance(result, dict):
        sha = result.get("commit_sha") or result.get("commit") or ""
        evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else \
            {k: v for k, v in result.items() if k not in ("commit_sha", "commit")}
        return VerifiedCommit(node.id, sha, evidence)
    raise TaskExecutorIntegrationError(
        f"run_leaf for {node.id} returned {type(result).__name__}, expected VerifiedCommit/dict/str")
