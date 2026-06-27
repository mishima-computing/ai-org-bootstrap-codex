#!/usr/bin/env python3
"""TaskExecutor: TRUE recursion (execute -> execute) + a verified commit PER NODE.

Two proofs, no carriers:
  * test_true_recursion_commit_per_node — a 2-level TaskGraph (root -> A, B; A -> A1, A2; B -> B1, B2)
    with STUBBED leaf-execute + verify (+ integrate/commit). Asserts execute(root) recursed into every leaf,
    each composite (A, B) returned its OWN integration commit, root returned a final integration commit,
    and execute called itself recursively (TaskExecutor -> TaskExecutor via the recorded parent->child edges).
  * test_real_git_integration — the SAME shape against a real temp git repo with the DEFAULT git
    machinery (cherry-pick integration + commit-tree), stubbing only the leaf runner to make real commits.
    Proves commit-per-node with real SHAs and that integration carries each subtree's files.

Run:  python3 scripts/test_executer.py
"""
from __future__ import annotations

import os
import concurrent.futures
import inspect
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import task_executor as S


# --------------------------------------------------------------------------------------------------
# the shared 2-level graph: root -> A, B ; A -> A1, A2 ; B -> B1, B2
# --------------------------------------------------------------------------------------------------
def _build_graph(base_sha=None) -> S.TaskGraph:
    a = S.TaskNode("A", kind=S.COMPOSITE, subtasks=[
        S.TaskNode("A1", kind=S.LEAF, objective="do A1"),
        S.TaskNode("A2", kind=S.LEAF, objective="do A2", depends_on=["A1"]),
    ], objective="compose A")
    b = S.TaskNode("B", kind=S.COMPOSITE, subtasks=[
        S.TaskNode("B1", kind=S.LEAF, objective="do B1"),
        S.TaskNode("B2", kind=S.LEAF, objective="do B2"),
    ], objective="compose B")
    root = S.TaskNode("root", kind=S.COMPOSITE, subtasks=[a, b], base_sha=base_sha, objective="the goal")
    return S.TaskGraph(root)


# --------------------------------------------------------------------------------------------------
# 1) pure recursion: stub leaf-execute + verify + integrate/commit; no git, no carriers
# --------------------------------------------------------------------------------------------------
def test_true_recursion_commit_per_node():
    leaves_executed: list[str] = []
    verified: list[str] = []
    leaf_commits: dict[str, S.VerifiedCommit] = {}
    integ_commits: dict[str, str] = {}

    def run_leaf(node):
        leaves_executed.append(node.id)
        vc = S.VerifiedCommit(node.id, f"leaf-sha-{node.id}", {"kind": "leaf"})
        leaf_commits[node.id] = vc
        return vc

    def verify(node, integrated_head, child_commits):
        verified.append(node.id)
        return {"verified": True, "node": node.id, "n_children": len(child_commits)}

    def integrate(node, base, child_commits):
        # no git: just hand back a synthetic integrated head + a None worktree
        return (f"integrated-head-{node.id}", None)

    def commit_integration(node, base, integrated_head, integ_wt, evidence):
        sha = f"integ-sha-{node.id}"
        integ_commits[node.id] = sha
        return sha

    # a synthetic base so the (git-free) pure run never resolves HEAD; children inherit it on descent
    graph = _build_graph(base_sha="BASE0")
    task_executor = S.TaskExecutor(run_leaf=run_leaf, verify=verify, integrate=integrate,
                      commit_integration=commit_integration, max_parallel=1)
    result = task_executor.execute(graph.root)

    # --- recursed into EVERY leaf ---
    assert sorted(leaves_executed) == ["A1", "A2", "B1", "B2"], leaves_executed

    # --- execute called itself recursively: TaskExecutor -> TaskExecutor (the recorded parent->child edges) ---
    edges = set(task_executor.recursion_edges)
    assert edges == {("root", "A"), ("root", "B"),
                     ("A", "A1"), ("A", "A2"),
                     ("B", "B1"), ("B", "B2")}, task_executor.recursion_edges
    # the composites were entered before their children (true descent, not a flat loop)
    assert task_executor.calls.index("A") < task_executor.calls.index("A1"), task_executor.calls
    assert task_executor.calls.index("B") < task_executor.calls.index("B1"), task_executor.calls
    assert set(task_executor.calls) == {"root", "A", "B", "A1", "A2", "B1", "B2"}, task_executor.calls

    # --- each composite (A, B) returned its OWN integration commit; root a final integration commit ---
    assert integ_commits == {"root": "integ-sha-root", "A": "integ-sha-A", "B": "integ-sha-B"}, integ_commits
    assert result.task_id == "root"
    assert result.commit_sha == "integ-sha-root", result
    # root integrated A's and B's OWN integration commits (topological order: A before B)
    assert result.evidence["integrated_children"] == ["integ-sha-A", "integ-sha-B"], result.evidence
    # internal nodes integrate + VERIFY + commit: every composite was verified over its integrated head
    assert sorted(verified) == ["A", "B", "root"], verified

    # --- a commit PER NODE: 7 nodes, 7 distinct commits (4 leaf + 3 integration) ---
    all_shas = {**{k: v.commit_sha for k, v in leaf_commits.items()}, **integ_commits}
    assert set(all_shas) == {"root", "A", "B", "A1", "A2", "B1", "B2"}, all_shas
    assert len(set(all_shas.values())) == 7, all_shas

    print("ok  true recursion (execute->execute) + commit-per-node (4 leaf + 3 integration) [pure, stubbed]")


# --------------------------------------------------------------------------------------------------
# 2) real git: default cherry-pick integration + commit-tree; stub only the leaf runner
# --------------------------------------------------------------------------------------------------
def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def _temp_git_repo(root) -> str:
    repo = Path(root) / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "seed.txt").write_text("seed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return str(repo)


def _worktree_paths(repo) -> list[str]:
    out = _git(repo, "worktree", "list", "--porcelain")
    return [line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("worktree ")]


def _assert_only_main_worktree(repo):
    paths = _worktree_paths(repo)
    assert [str(Path(p).resolve()) for p in paths] == [str(Path(repo).resolve())], paths


def _task_branch_refs(repo) -> list[str]:
    out = subprocess.run(["git", "-C", str(repo), "for-each-ref", "--format=%(refname:short)",
                          "refs/heads/ai-org/tasks"],
                         check=True, capture_output=True, text=True).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def _make_child_commit(repo, base: str, rel: str, content: str) -> str:
    wt = Path(tempfile.mkdtemp(prefix="t-child-commit-"))
    _git(repo, "worktree", "add", "--detach", str(wt), base)
    try:
        (wt / rel).write_text(content)
        _git(wt, "add", "--", rel)
        tree = _git(wt, "write-tree").strip()
        return _git(repo, "commit-tree", tree, "-p", base, "-m", f"child {rel}").strip()
    finally:
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)],
                       capture_output=True)
        shutil.rmtree(wt, ignore_errors=True)


def _make_empty_child_commit(repo, base: str) -> str:
    tree = _git(repo, "rev-parse", f"{base}^{{tree}}").strip()
    return _git(repo, "commit-tree", tree, "-p", base, "-m", "empty child").strip()


def _is_sha(s: str) -> bool:
    return isinstance(s, str) and len(s) in (40, 64) and all(c in "0123456789abcdef" for c in s)


def test_real_git_integration():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _temp_git_repo(tmp)

        # stub leaf runner: make a REAL commit (one file per leaf) off the leaf's base_sha, returning a
        # cherry-pickable VerifiedCommit. The default integrate/commit_integration/verify run for real.
        leaf_shas: dict[str, str] = {}

        def run_leaf(node):
            base = node.base_sha
            wt = Path(tempfile.mkdtemp(prefix=f"t-leaf-{node.id}-"))
            _git(repo, "worktree", "add", "--detach", str(wt), base)
            try:
                (wt / f"{node.id}.txt").write_text(f"{node.id} content\n")
                _git(wt, "add", "--", f"{node.id}.txt")
                tree = _git(wt, "write-tree").strip()
                sha = _git(repo, "commit-tree", tree, "-p", base, "-m", f"leaf: {node.id}").strip()
                leaf_shas[node.id] = sha
                return S.VerifiedCommit(node.id, sha, {"file": f"{node.id}.txt"})
            finally:
                subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", str(wt)],
                               capture_output=True)

        graph = _build_graph()                                  # base_sha=None -> task_executor resolves HEAD
        task_executor = S.TaskExecutor(repo, run_leaf=run_leaf, max_parallel=1)   # real default integrate/verify/commit
        result = task_executor.execute(graph.root)

        # recursion happened over the whole tree
        assert set(task_executor.recursion_edges) == {("root", "A"), ("root", "B"),
                                               ("A", "A1"), ("A", "A2"),
                                               ("B", "B1"), ("B", "B2")}, task_executor.recursion_edges
        assert sorted(leaf_shas) == ["A1", "A2", "B1", "B2"], leaf_shas

        # commit-per-node with REAL shas: 4 leaf + 2 sub-integration + 1 root, all distinct
        int_a, int_b = result.evidence["integrated_children"]
        root_sha = result.commit_sha
        real = list(leaf_shas.values()) + [int_a, int_b, root_sha]
        assert all(_is_sha(s) for s in real), real
        assert len(set(real)) == 7, ("a distinct commit per node", real)

        # each composite's integration carries its OWN subtree's files (A -> a1,a2 ; B -> b1,b2)
        files_a = _git(repo, "ls-tree", "-r", "--name-only", int_a).split()
        files_b = _git(repo, "ls-tree", "-r", "--name-only", int_b).split()
        assert {"A1.txt", "A2.txt"} <= set(files_a) and "B1.txt" not in files_a, files_a
        assert {"B1.txt", "B2.txt"} <= set(files_b) and "A1.txt" not in files_b, files_b

        # the ROOT integration commit carries the WHOLE tree (all four leaves integrated)
        files_root = _git(repo, "ls-tree", "-r", "--name-only", root_sha).split()
        assert {"A1.txt", "A2.txt", "B1.txt", "B2.txt", "seed.txt"} <= set(files_root), files_root

        # the user's working tree / HEAD was never disturbed (integration ran in throwaway worktrees)
        assert _git(repo, "status", "--porcelain").strip() == "", "working tree must stay clean"
        _assert_only_main_worktree(repo)

        print("ok  real-git recursion: cherry-pick integration + commit-tree -> real commit-per-node")


def test_parallel_independent_tasks_integrate_as_isolated_branches():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _temp_git_repo(tmp)
        started = {"A": threading.Event(), "B": threading.Event()}

        def run_leaf(node):
            started[node.id].set()
            other = "B" if node.id == "A" else "A"
            assert started[other].wait(5), f"{node.id} did not overlap with independent sibling"
            return S.VerifiedCommit(
                node.id,
                _make_child_commit(repo, node.base_sha, f"{node.id}.txt", f"{node.id}\n"),
                {"kind": "leaf"},
            )

        root = S.TaskNode("root", kind=S.COMPOSITE, base_sha=_git(repo, "rev-parse", "HEAD").strip(),
                          objective="compose", subtasks=[
            S.TaskNode("A", kind=S.LEAF, objective="do A"),
            S.TaskNode("B", kind=S.LEAF, objective="do B"),
        ])
        task_executor = S.TaskExecutor(repo, run_leaf=run_leaf, max_parallel=2)
        result = task_executor.execute(root)

        files_root = set(_git(repo, "ls-tree", "-r", "--name-only", result.commit_sha).split())
        assert {"A.txt", "B.txt", "seed.txt"} <= files_root, files_root
        refs = _task_branch_refs(repo)
        assert any("/A-" in ref for ref in refs) and any("/B-" in ref for ref in refs), refs
        _assert_only_main_worktree(repo)
    print("ok  independent tasks run concurrently, publish isolated task branches, and integrate")


# --------------------------------------------------------------------------------------------------
# 3) commit threading: a SERIAL child resumes from its dependency's OUTPUT commit (recorded-base assert)
#    — independent siblings still cut from the parent base. Uses recorded bases so disjoint files can't
#    mask the bug the way the real-git test does.
# --------------------------------------------------------------------------------------------------
def test_serial_child_inherits_dependency_output_commit():
    recorded_base: dict[str, str] = {}
    leaf_commits: dict[str, str] = {}

    def run_leaf(node):
        recorded_base[node.id] = node.base_sha          # RECORD the base each leaf was actually cut from
        sha = f"leaf-sha-{node.id}"
        leaf_commits[node.id] = sha
        return S.VerifiedCommit(node.id, sha, {"kind": "leaf"})

    def verify(node, integrated_head, child_commits):
        return {"verified": True}

    def integrate(node, base, child_commits):
        return (f"integrated-head-{node.id}", None)

    def commit_integration(node, base, integrated_head, integ_wt, evidence):
        return f"integ-sha-{node.id}"

    # P -> A (independent), B (depends_on A), C (independent)
    p = S.TaskNode("P", kind=S.COMPOSITE, base_sha="BASE0", objective="compose P", subtasks=[
        S.TaskNode("A", kind=S.LEAF, objective="do A"),
        S.TaskNode("B", kind=S.LEAF, objective="do B", depends_on=["A"]),
        S.TaskNode("C", kind=S.LEAF, objective="do C"),
    ])
    task_executor = S.TaskExecutor(run_leaf=run_leaf, verify=verify, integrate=integrate,
                      commit_integration=commit_integration, max_parallel=1)
    task_executor.execute(p)

    # independent siblings A and C cut from the PARENT base — NO inheritance (the parallel case, kept)
    assert recorded_base["A"] == "BASE0", recorded_base
    assert recorded_base["C"] == "BASE0", recorded_base
    # the SERIAL child B RESUMES from A's OUTPUT commit, NOT the parent base (the fix)
    assert recorded_base["B"] == leaf_commits["A"], recorded_base
    assert recorded_base["B"] != "BASE0", recorded_base

    print("ok  serial child resumes from its dependency's output commit; independent siblings use parent base")


def test_dependency_forces_branch_base_and_integration_order():
    ordered_leaves: list[str] = []
    integrated_orders: list[list[str]] = []

    def run_leaf(node):
        ordered_leaves.append(node.id)
        return S.VerifiedCommit(node.id, f"leaf-sha-{node.id}", {"kind": "leaf"})

    def verify(_node, _integrated_head, _child_commits):
        return {"verified": True}

    def integrate(node, base, child_commits):
        integrated_orders.append([c.task_id for c in child_commits])
        return (f"integrated-head-{node.id}", None)

    def commit_integration(node, base, integrated_head, integ_wt, evidence):
        return f"integ-sha-{node.id}"

    root = S.TaskNode("root", kind=S.COMPOSITE, base_sha="BASE0", objective="compose", subtasks=[
        S.TaskNode("A", kind=S.LEAF, objective="do A"),
        S.TaskNode("B", kind=S.LEAF, objective="do B", depends_on=["A"]),
    ])
    task_executor = S.TaskExecutor(run_leaf=run_leaf, verify=verify, integrate=integrate,
                      commit_integration=commit_integration, max_parallel=2)
    result = task_executor.execute(root)

    assert ordered_leaves == ["A", "B"], ordered_leaves
    assert integrated_orders[-1] == ["A", "B"], integrated_orders
    assert result.evidence["integrated_children"] == ["leaf-sha-A", "leaf-sha-B"], result.evidence
    print("ok  dependency forces task branch base selection and controller integration order")


def test_duplicate_sibling_task_ids_fail_closed():
    leaves_executed: list[str] = []

    def run_leaf(node):
        leaves_executed.append(node.id)
        return S.VerifiedCommit(node.id, f"leaf-sha-{node.id}", {"kind": "leaf"})

    root = S.TaskNode("root", kind=S.COMPOSITE, base_sha="BASE0", objective="compose", subtasks=[
        S.TaskNode("dup", kind=S.LEAF, objective="do first"),
        S.TaskNode("dup", kind=S.LEAF, objective="do second"),
    ])
    task_executor = S.TaskExecutor(run_leaf=run_leaf, verify=lambda *_args: {"verified": True},
                      integrate=lambda node, base, commits: (f"integrated-{node.id}", None),
                      commit_integration=lambda node, base, head, wt, evidence: f"integ-{node.id}",
                      max_parallel=1)
    try:
        task_executor.execute(root)
        raise AssertionError("duplicate sibling ids must fail closed")
    except S.TaskExecutorIntegrationError as exc:
        assert "duplicate sibling task ids" in str(exc), exc
    assert leaves_executed == [], leaves_executed
    print("ok  duplicate sibling task ids fail closed before result-map overwrite")


def test_dependency_cycle_fails_closed():
    root = S.TaskNode("root", kind=S.COMPOSITE, base_sha="BASE0", objective="compose", subtasks=[
        S.TaskNode("A", kind=S.LEAF, objective="do A", depends_on=["B"]),
        S.TaskNode("B", kind=S.LEAF, objective="do B", depends_on=["A"]),
    ])
    task_executor = S.TaskExecutor(run_leaf=lambda node: S.VerifiedCommit(node.id, f"leaf-{node.id}", {}),
                      verify=lambda *_args: {"verified": True},
                      integrate=lambda node, base, commits: (f"integrated-{node.id}", None),
                      commit_integration=lambda node, base, head, wt, evidence: f"integ-{node.id}",
                      max_parallel=2)
    try:
        task_executor.execute(root)
        raise AssertionError("dependency cycle must raise")
    except S.TaskExecutorIntegrationError as exc:
        assert "dependency cycle" in str(exc), exc
    print("ok  dependency cycles fail closed before dispatch/integration")


def test_depends_on_unknown_sibling_id_fails_closed():
    leaves_executed: list[str] = []

    def run_leaf(node):
        leaves_executed.append(node.id)
        return S.VerifiedCommit(node.id, f"leaf-sha-{node.id}", {"kind": "leaf"})

    root = S.TaskNode("root", kind=S.COMPOSITE, base_sha="BASE0", objective="compose", subtasks=[
        S.TaskNode("A", kind=S.LEAF, objective="do A"),
        S.TaskNode("B", kind=S.LEAF, objective="do B", depends_on=["TypoA"]),
    ])
    task_executor = S.TaskExecutor(run_leaf=run_leaf, verify=lambda *_args: {"verified": True},
                      integrate=lambda node, base, commits: (f"integrated-{node.id}", None),
                      commit_integration=lambda node, base, head, wt, evidence: f"integ-{node.id}",
                      max_parallel=2)
    try:
        task_executor.execute(root)
        raise AssertionError("unknown depends_on id must fail closed")
    except S.TaskExecutorIntegrationError as exc:
        assert "task 'B' depends_on unknown id 'TypoA'" in str(exc), exc
    assert leaves_executed == [], leaves_executed
    print("ok  unknown depends_on sibling id fails closed before parallel dispatch")


def test_depends_on_cross_level_id_fails_closed():
    leaves_executed: list[str] = []

    def run_leaf(node):
        leaves_executed.append(node.id)
        return S.VerifiedCommit(node.id, f"leaf-sha-{node.id}", {"kind": "leaf"})

    root = S.TaskNode("root", kind=S.COMPOSITE, base_sha="BASE0", objective="compose", subtasks=[
        S.TaskNode("A", kind=S.COMPOSITE, objective="compose A", subtasks=[
            S.TaskNode("A1", kind=S.LEAF, objective="do A1"),
        ]),
        S.TaskNode("B", kind=S.LEAF, objective="do B", depends_on=["A1"]),
    ])
    task_executor = S.TaskExecutor(run_leaf=run_leaf, verify=lambda *_args: {"verified": True},
                      integrate=lambda node, base, commits: (f"integrated-{node.id}", None),
                      commit_integration=lambda node, base, head, wt, evidence: f"integ-{node.id}",
                      max_parallel=2)
    try:
        task_executor.execute(root)
        raise AssertionError("cross-level depends_on id must fail closed")
    except S.TaskExecutorIntegrationError as exc:
        assert "task 'B' depends_on unknown id 'A1'" in str(exc), exc
    assert leaves_executed == [], leaves_executed
    print("ok  cross-level depends_on id is rejected as a non-sibling dependency")


# --------------------------------------------------------------------------------------------------
# 4) multi-dep threading: a child depending on SEVERAL siblings resumes from their INTEGRATED head
# --------------------------------------------------------------------------------------------------
def test_serial_child_with_multiple_deps_resumes_from_integrated_head():
    recorded_base: dict[str, str] = {}
    leaf_commits: dict[str, str] = {}
    integrate_calls: list[list[str]] = []

    def run_leaf(node):
        recorded_base[node.id] = node.base_sha
        sha = f"leaf-sha-{node.id}"
        leaf_commits[node.id] = sha
        return S.VerifiedCommit(node.id, sha, {"kind": "leaf"})

    def verify(node, integrated_head, child_commits):
        return {"verified": True}

    def integrate(node, base, child_commits):
        ids = [c.task_id for c in child_commits]
        integrate_calls.append(ids)
        return ("integ-head-" + "+".join(ids), None)      # synthetic integrated head; no worktree

    def commit_integration(node, base, integrated_head, integ_wt, evidence):
        return f"integ-sha-{node.id}"

    # P -> A, B (independent), D (depends_on A AND B)
    p = S.TaskNode("P", kind=S.COMPOSITE, base_sha="BASE0", objective="compose P", subtasks=[
        S.TaskNode("A", kind=S.LEAF, objective="do A"),
        S.TaskNode("B", kind=S.LEAF, objective="do B"),
        S.TaskNode("D", kind=S.LEAF, objective="do D", depends_on=["A", "B"]),
    ])
    task_executor = S.TaskExecutor(run_leaf=run_leaf, verify=verify, integrate=integrate,
                      commit_integration=commit_integration, max_parallel=1)
    task_executor.execute(p)

    assert recorded_base["A"] == "BASE0" and recorded_base["B"] == "BASE0", recorded_base
    # D resumes from the INTEGRATED head of its two deps' commits (deps integrated before D runs)
    assert recorded_base["D"] == "integ-head-A+B", recorded_base
    # _integrate was invoked to pre-integrate D's deps (A+B), distinct from P's final child integration
    assert ["A", "B"] in integrate_calls, integrate_calls

    print("ok  multi-dep child resumes from its dependencies' integrated head")


def test_multi_dep_base_preintegration_uses_topo_order_not_literal_order():
    recorded_base: dict[str, str] = {}
    integrate_calls: list[list[str]] = []

    def run_leaf(node):
        recorded_base[node.id] = node.base_sha
        return S.VerifiedCommit(node.id, f"leaf-sha-{node.id}", {"kind": "leaf"})

    def verify(node, integrated_head, child_commits):
        return {"verified": True}

    def integrate(node, base, child_commits):
        ids = [c.task_id for c in child_commits]
        integrate_calls.append(ids)
        return ("integ-head-" + "+".join(ids), None)

    def commit_integration(node, base, integrated_head, integ_wt, evidence):
        return f"integ-sha-{node.id}"

    # D lists ["B", "A"], but B depends on A. D's pre-integration base must integrate A before B.
    p = S.TaskNode("P", kind=S.COMPOSITE, base_sha="BASE0", objective="compose P", subtasks=[
        S.TaskNode("A", kind=S.LEAF, objective="do A"),
        S.TaskNode("B", kind=S.LEAF, objective="do B", depends_on=["A"]),
        S.TaskNode("D", kind=S.LEAF, objective="do D", depends_on=["B", "A"]),
    ])
    task_executor = S.TaskExecutor(run_leaf=run_leaf, verify=verify, integrate=integrate,
                      commit_integration=commit_integration, max_parallel=1)
    task_executor.execute(p)

    assert recorded_base["A"] == "BASE0", recorded_base
    assert recorded_base["B"] == "leaf-sha-A", recorded_base
    assert recorded_base["D"] == "integ-head-A+B", recorded_base
    assert ["A", "B"] in integrate_calls, integrate_calls
    assert ["B", "A"] not in integrate_calls, integrate_calls
    print("ok  multi-dep base pre-integration uses dependency topo order, not literal depends_on order")


def test_nested_parallel_composites_complete_under_timeout():
    leaves_executed: list[str] = []
    leaf_guard = threading.Lock()

    def run_leaf(node):
        time.sleep(0.02)
        with leaf_guard:
            leaves_executed.append(node.id)
        return S.VerifiedCommit(node.id, f"leaf-sha-{node.id}", {"kind": "leaf"})

    def verify(node, integrated_head, child_commits):
        return {"verified": True, "node": node.id, "n_children": len(child_commits)}

    def integrate(node, base, child_commits):
        time.sleep(0.01)
        return (f"integrated-head-{node.id}", None)

    def commit_integration(node, base, integrated_head, integ_wt, evidence):
        return f"integ-sha-{node.id}"

    root = S.TaskNode("root", kind=S.COMPOSITE, base_sha="BASE0", objective="nested parallel", subtasks=[
        S.TaskNode("A", kind=S.COMPOSITE, objective="compose A", subtasks=[
            S.TaskNode("A1", kind=S.LEAF, objective="do A1"),
            S.TaskNode("A2", kind=S.LEAF, objective="do A2"),
        ]),
        S.TaskNode("B", kind=S.COMPOSITE, objective="compose B", subtasks=[
            S.TaskNode("B1", kind=S.LEAF, objective="do B1"),
            S.TaskNode("B2", kind=S.LEAF, objective="do B2"),
        ]),
    ])
    task_executor = S.TaskExecutor(run_leaf=run_leaf, verify=verify, integrate=integrate,
                      commit_integration=commit_integration, max_parallel=2)
    result_holder: dict[str, S.VerifiedCommit] = {}
    error_holder: dict[str, BaseException] = {}

    def run_execute():
        try:
            result_holder["result"] = task_executor.execute(root)
        except BaseException as exc:  # noqa: BLE001 - relay worker failure to the test thread
            error_holder["error"] = exc

    thread = threading.Thread(target=run_execute, daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive(), "nested parallel composite execution hung"
    if error_holder:
        raise error_holder["error"]

    assert result_holder["result"].commit_sha == "integ-sha-root", result_holder
    assert sorted(leaves_executed) == ["A1", "A2", "B1", "B2"], leaves_executed
    assert set(task_executor.recursion_edges) == {("root", "A"), ("root", "B"),
                                                  ("A", "A1"), ("A", "A2"),
                                                  ("B", "B1"), ("B", "B2")}, task_executor.recursion_edges
    assert set(task_executor.calls) == {"root", "A", "B", "A1", "A2", "B1", "B2"}, task_executor.calls
    assert task_executor.calls.index("A") < task_executor.calls.index("A1"), task_executor.calls
    assert task_executor.calls.index("B") < task_executor.calls.index("B1"), task_executor.calls
    print("ok  nested parallel composites complete under timeout with recursion trace intact")


def test_default_max_depth_is_floor_when_env_unset():
    old = os.environ.pop("AI_ORG_MAX_DEPTH", None)
    try:
        task_executor = S.TaskExecutor(max_parallel=1)
        assert task_executor.max_depth == S.FLOOR_MAX_DEPTH, task_executor.max_depth
    finally:
        if old is not None:
            os.environ["AI_ORG_MAX_DEPTH"] = old

    print("ok  TaskExecutor defaults max_depth to FLOOR_MAX_DEPTH when AI_ORG_MAX_DEPTH is unset")


def test_root_decompose_result_sets_max_depth_once():
    root = S.TaskNode("root", kind=S.COMPOSITE, base_sha="BASE0", objective="large recursive feature")
    leaves_executed: list[tuple[str, int]] = []

    def decomposer(node):
        if node.depth == 0:
            return S.DecomposeResult([
                S.TaskNode("root.child", kind=S.COMPOSITE, base_sha=node.base_sha,
                           objective="split again", depth=node.depth + 1),
            ], max_depth=2)
        return S.DecomposeResult([
            S.TaskNode(f"{node.id}.child", kind=S.COMPOSITE, base_sha=node.base_sha,
                       objective="split again", depth=node.depth + 1),
        ], max_depth=5)

    def run_leaf(node):
        leaves_executed.append((node.id, node.depth))
        return S.VerifiedCommit(node.id, f"leaf-sha-{node.id}", {"kind": "leaf"})

    def verify(node, integrated_head, child_commits):
        return {"verified": True}

    def integrate(node, base, child_commits):
        return (f"integrated-head-{node.id}", None)

    def commit_integration(node, base, integrated_head, integ_wt, evidence):
        return f"integ-sha-{node.id}"

    task_executor = S.TaskExecutor(run_leaf=run_leaf, verify=verify, integrate=integrate,
                      commit_integration=commit_integration, decomposer=decomposer,
                      max_parallel=1, max_depth=3)
    task_executor.execute(root)

    assert task_executor.max_depth == 2, task_executor.max_depth
    assert leaves_executed == [("root.child.child", 2)], leaves_executed
    print("ok  root DecomposeResult sets max_depth once; non-root metadata is ignored")


def test_failing_composite_verify_blocks_integration_commit():
    committed: list[str] = []

    def run_leaf(node):
        return S.VerifiedCommit(node.id, f"leaf-sha-{node.id}", {"kind": "leaf"})

    def verify(node, integrated_head, child_commits):
        return {"verified": False, "finding": "integrated result was not accepted"}

    def integrate(node, base, child_commits):
        return (f"integrated-head-{node.id}", None)

    def commit_integration(node, base, integrated_head, integ_wt, evidence):
        committed.append(node.id)
        return f"integ-sha-{node.id}"

    root = S.TaskNode("root", kind=S.COMPOSITE, base_sha="BASE0", objective="compose", subtasks=[
        S.TaskNode("a", kind=S.LEAF, objective="do a"),
    ])
    task_executor = S.TaskExecutor(run_leaf=run_leaf, verify=verify, integrate=integrate,
                      commit_integration=commit_integration, max_parallel=1)
    try:
        task_executor.execute(root)
        raise AssertionError("a failing composite verify must not silently pass")
    except S.TaskExecutorIntegrationError as exc:
        assert "composite verification failed" in str(exc), exc
    assert committed == [], committed
    print("ok  failing composite verify blocks integration commit")


def test_cherry_pick_conflict_is_detected_and_aborts_integration():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _temp_git_repo(tmp)
        base = _git(repo, "rev-parse", "HEAD").strip()
        Path(repo, "shared.txt").write_text("base\n", encoding="utf-8")
        _git(repo, "add", "shared.txt")
        _git(repo, "commit", "-q", "-m", "shared base")
        base = _git(repo, "rev-parse", "HEAD").strip()

        def run_leaf(node):
            return S.VerifiedCommit(
                node.id,
                _make_child_commit(repo, node.base_sha, "shared.txt", f"{node.id}\n"),
                {"kind": "leaf"},
            )

        root = S.TaskNode("root", kind=S.COMPOSITE, base_sha=base, objective="compose", subtasks=[
            S.TaskNode("A", kind=S.LEAF, objective="write A"),
            S.TaskNode("B", kind=S.LEAF, objective="write B"),
        ])
        task_executor = S.TaskExecutor(repo, run_leaf=run_leaf, max_parallel=2)
        try:
            task_executor.execute(root)
            raise AssertionError("conflicting task branches must not be silently dropped")
        except S.TaskExecutorIntegrationError as exc:
            assert "cherry-pick failed" in str(exc) and "B" in str(exc), exc
        _assert_only_main_worktree(repo)
    print("ok  textual cherry-pick conflict is detected, aborted, and reported")


def test_semantic_conflict_is_caught_by_integration_verifier():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _temp_git_repo(tmp)
        base = _git(repo, "rev-parse", "HEAD").strip()

        def run_leaf(node):
            rel = "a.flag" if node.id == "A" else "b.flag"
            return S.VerifiedCommit(
                node.id,
                _make_child_commit(repo, node.base_sha, rel, "enabled\n"),
                {"kind": "leaf"},
            )

        def verify(_node, integrated_head, _child_commits):
            files = set(_git(repo, "ls-tree", "-r", "--name-only", integrated_head).split())
            both_enabled = {"a.flag", "b.flag"} <= files
            return {"verified": not both_enabled, "method": "integration-test",
                    "finding": "a.flag and b.flag cannot both be enabled"}

        root = S.TaskNode("root", kind=S.COMPOSITE, base_sha=base, objective="compose", subtasks=[
            S.TaskNode("A", kind=S.LEAF, objective="enable a"),
            S.TaskNode("B", kind=S.LEAF, objective="enable b"),
        ])
        task_executor = S.TaskExecutor(repo, run_leaf=run_leaf, verify=verify, max_parallel=2)
        try:
            task_executor.execute(root)
            raise AssertionError("semantic conflict must fail integration verification")
        except S.TaskExecutorIntegrationError as exc:
            assert "composite verification failed" in str(exc), exc
            assert "integration-test" in str(exc), exc
        _assert_only_main_worktree(repo)
    print("ok  semantic merge conflict is caught by integration verification")


def test_mutable_child_base_ref_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _temp_git_repo(tmp)
        base = _git(repo, "rev-parse", "HEAD").strip()
        leaves_executed: list[str] = []

        def run_leaf(node):
            leaves_executed.append(node.id)
            return S.VerifiedCommit(
                node.id,
                _make_child_commit(repo, node.base_sha, f"{node.id}.txt", f"{node.id}\n"),
                {"kind": "leaf"},
            )

        root = S.TaskNode("root", kind=S.COMPOSITE, base_sha=base, objective="compose", subtasks=[
            S.TaskNode("A", kind=S.LEAF, base_sha="main", objective="mutable base"),
        ])
        task_executor = S.TaskExecutor(repo, run_leaf=run_leaf, max_parallel=1)
        try:
            task_executor.execute(root)
            raise AssertionError("mutable base ref must be rejected")
        except S.TaskExecutorIntegrationError as exc:
            assert "immutable full commit SHA" in str(exc), exc
        assert leaves_executed == [], leaves_executed
        assert _task_branch_refs(repo) == [], _task_branch_refs(repo)
        _assert_only_main_worktree(repo)
    print("ok  mutable child base ref is rejected before branch execution")


def test_rerun_child_base_uses_current_parent_base_not_stale_child_base():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _temp_git_repo(tmp)
        stale_base = _git(repo, "rev-parse", "HEAD").strip()
        Path(repo, "fresh.txt").write_text("fresh\n", encoding="utf-8")
        _git(repo, "add", "fresh.txt")
        _git(repo, "commit", "-q", "-m", "fresh parent")
        fresh_base = _git(repo, "rev-parse", "HEAD").strip()
        recorded_base: dict[str, str] = {}

        def run_leaf(node):
            recorded_base[node.id] = node.base_sha
            return S.VerifiedCommit(
                node.id,
                _make_child_commit(repo, node.base_sha, f"{node.id}.txt", f"{node.id}\n"),
                {"kind": "leaf"},
            )

        child = S.TaskNode("A", kind=S.LEAF, base_sha=stale_base, objective="rerun stale base")
        root = S.TaskNode("root", kind=S.COMPOSITE, objective="compose", subtasks=[child])
        task_executor = S.TaskExecutor(repo, run_leaf=run_leaf, max_parallel=1)
        task_executor.execute(root)

        assert recorded_base["A"] == fresh_base, recorded_base
        assert child.base_sha == stale_base, "planning must not overwrite the shared TaskNode base"
        _assert_only_main_worktree(repo)
    print("ok  rerun child base is recomputed from current parent base, not stale child.base_sha")


def test_inconsistent_inherited_child_base_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _temp_git_repo(tmp)
        base = _git(repo, "rev-parse", "HEAD").strip()
        _git(repo, "checkout", "-q", "-b", "side", base)
        Path(repo, "side.txt").write_text("side\n", encoding="utf-8")
        _git(repo, "add", "side.txt")
        _git(repo, "commit", "-q", "-m", "side")
        side_sha = _git(repo, "rev-parse", "HEAD").strip()
        _git(repo, "checkout", "-q", "main")
        Path(repo, "main.txt").write_text("main\n", encoding="utf-8")
        _git(repo, "add", "main.txt")
        _git(repo, "commit", "-q", "-m", "main")
        main_sha = _git(repo, "rev-parse", "HEAD").strip()
        leaves_executed: list[str] = []

        def run_leaf(node):
            leaves_executed.append(node.id)
            return S.VerifiedCommit(node.id, main_sha, {"kind": "leaf"})

        root = S.TaskNode("root", kind=S.COMPOSITE, base_sha=main_sha, objective="compose", subtasks=[
            S.TaskNode("A", kind=S.LEAF, base_sha=side_sha, objective="unrelated inherited base"),
        ])
        task_executor = S.TaskExecutor(repo, run_leaf=run_leaf, max_parallel=1)
        try:
            task_executor.execute(root)
            raise AssertionError("unrelated inherited child base must fail closed")
        except S.TaskExecutorIntegrationError as exc:
            assert "is not an ancestor of current parent base" in str(exc), exc
        assert leaves_executed == [], leaves_executed
        assert _task_branch_refs(repo) == [], _task_branch_refs(repo)
        _assert_only_main_worktree(repo)
    print("ok  inconsistent inherited child base fails closed before dispatch")


def test_failed_task_branch_ref_is_deleted():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _temp_git_repo(tmp)
        base = _git(repo, "rev-parse", "HEAD").strip()

        def run_leaf(node):
            raise RuntimeError(f"task {node.id} failed")

        root = S.TaskNode("root", kind=S.COMPOSITE, base_sha=base, objective="compose", subtasks=[
            S.TaskNode("fail", kind=S.LEAF, objective="fail"),
        ])
        task_executor = S.TaskExecutor(repo, run_leaf=run_leaf, max_parallel=1)
        try:
            task_executor.execute(root)
            raise AssertionError("failing branch task must raise")
        except RuntimeError as exc:
            assert "task fail failed" in str(exc), exc
        assert _task_branch_refs(repo) == [], _task_branch_refs(repo)
        _assert_only_main_worktree(repo)
    print("ok  failed task branch refs are deleted")


def test_redundant_non_empty_child_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _temp_git_repo(tmp)
        base = _git(repo, "rev-parse", "HEAD").strip()

        def run_leaf(node):
            return S.VerifiedCommit(
                node.id,
                _make_child_commit(repo, node.base_sha, "same.txt", "same\n"),
                {"kind": "leaf"},
            )

        root = S.TaskNode("root", kind=S.COMPOSITE, base_sha=base, objective="compose", subtasks=[
            S.TaskNode("A", kind=S.LEAF, objective="write same"),
            S.TaskNode("B", kind=S.LEAF, objective="write same redundantly"),
        ])
        task_executor = S.TaskExecutor(repo, run_leaf=run_leaf, max_parallel=1)
        try:
            task_executor.execute(root)
            raise AssertionError("redundant non-empty child must fail closed")
        except S.TaskExecutorIntegrationError as exc:
            assert "suspicious redundant integration" in str(exc) and "B" in str(exc), exc
        _assert_only_main_worktree(repo)
    print("ok  non-empty child that changes no integrated tree fails closed")


def test_genuinely_empty_child_is_recorded_and_allowed():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _temp_git_repo(tmp)
        base = _git(repo, "rev-parse", "HEAD").strip()
        events: list[dict] = []
        empty_commit: dict[str, S.VerifiedCommit] = {}

        def run_leaf(node):
            vc = S.VerifiedCommit(node.id, _make_empty_child_commit(repo, node.base_sha), {"kind": "leaf"})
            empty_commit[node.id] = vc
            return vc

        root = S.TaskNode("root", kind=S.COMPOSITE, base_sha=base, objective="compose", subtasks=[
            S.TaskNode("empty", kind=S.LEAF, objective="no effective change"),
        ])
        task_executor = S.TaskExecutor(repo, run_leaf=run_leaf, max_parallel=1, emit=events.append)
        result = task_executor.execute(root)

        assert _is_sha(result.commit_sha), result
        assert empty_commit["empty"].evidence["empty_child_recorded"] is True, empty_commit
        assert any(e.get("type") == "integration_empty_child" and e.get("id") == "empty"
                   for e in events), events
        _assert_only_main_worktree(repo)
    print("ok  genuinely empty child is allowed but recorded")


def test_planned_unpublished_task_ref_is_deleted_on_abort():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _temp_git_repo(tmp)
        base = _git(repo, "rev-parse", "HEAD").strip()
        task_executor = S.TaskExecutor(repo, max_parallel=1)
        plan = S.PlannedBranchTask("planned", base, "ai-org/tasks/root/planned-test")
        task_executor._create_task_branch(plan.branch_name, plan.branch_base)
        assert plan.branch_name in _task_branch_refs(repo), _task_branch_refs(repo)

        task_executor._cleanup_unpublished_task_branches()

        assert plan.branch_name not in _task_branch_refs(repo), _task_branch_refs(repo)
        _assert_only_main_worktree(repo)
    print("ok  planned-but-unpublished task refs are deleted on abort cleanup")


def test_executor_model_has_no_shared_lock_across_blocking_wait():
    source = inspect.getsource(S.TaskExecutor)
    assert "threading.Lock" not in source, source
    assert "_trace_guard" not in source and "_resource_guard" not in source, source
    assert "future.result()" in source, "the regression check must cover the blocking wait site"
    assert "with self." not in source[source.find("future.result()") - 200:source.find("future.result()") + 80]
    print("ok  executor has no shared lock guard around Future.result blocking waits")


def test_default_leaf_exception_leaves_no_worktree():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _temp_git_repo(tmp)
        import controller_pipeline
        original = controller_pipeline.run_pipeline

        def raising_pipeline(*_args, **_kwargs):
            raise RuntimeError("carrier crashed mid-leaf")

        controller_pipeline.run_pipeline = raising_pipeline
        try:
            leaf = S.TaskNode("boom", kind=S.LEAF, base_sha=_git(repo, "rev-parse", "HEAD").strip(),
                              objective="raise after worktree add")
            task_executor = S.TaskExecutor(repo, max_parallel=1)
            try:
                task_executor.execute(leaf)
                raise AssertionError("default leaf exception must propagate")
            except RuntimeError as exc:
                assert "carrier crashed" in str(exc), exc
            _assert_only_main_worktree(repo)
        finally:
            controller_pipeline.run_pipeline = original
    print("ok  default leaf exception removes its worktree")


def test_composite_verify_failure_leaves_no_integration_worktree():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _temp_git_repo(tmp)
        base = _git(repo, "rev-parse", "HEAD").strip()

        def run_leaf(node):
            sha = _make_child_commit(repo, node.base_sha, f"{node.id}.txt", f"{node.id}\n")
            return S.VerifiedCommit(node.id, sha, {"kind": "leaf"})

        def verify(_node, _integrated_head, _child_commits):
            return {"verified": False, "finding": "reject integrated result"}

        root = S.TaskNode("root", kind=S.COMPOSITE, base_sha=base, objective="compose", subtasks=[
            S.TaskNode("a", kind=S.LEAF, objective="do a"),
        ])
        task_executor = S.TaskExecutor(repo, run_leaf=run_leaf, verify=verify, max_parallel=1)
        try:
            task_executor.execute(root)
            raise AssertionError("failing composite verify must raise")
        except S.TaskExecutorIntegrationError as exc:
            assert "composite verification failed" in str(exc), exc
        _assert_only_main_worktree(repo)
    print("ok  composite verify failure removes its integration worktree")


def test_worktree_cleanup_is_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _temp_git_repo(tmp)
        base = _git(repo, "rev-parse", "HEAD").strip()
        wt = Path(tempfile.mkdtemp(prefix="t-idempotent-wt-"))
        _git(repo, "worktree", "add", "--detach", str(wt), base)
        task_executor = S.TaskExecutor(repo, max_parallel=1)
        task_executor._cleanup_worktree(wt)
        task_executor._cleanup_worktree(wt)
        _assert_only_main_worktree(repo)
    print("ok  worktree cleanup is idempotent")


def test_worktree_cleanup_leaves_live_sibling_worktree_intact():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _temp_git_repo(tmp)
        base = _git(repo, "rev-parse", "HEAD").strip()
        wt_a = Path(tempfile.mkdtemp(prefix="t-clean-a-"))
        wt_b = Path(tempfile.mkdtemp(prefix="t-clean-b-"))
        _git(repo, "worktree", "add", "--detach", str(wt_a), base)
        _git(repo, "worktree", "add", "--detach", str(wt_b), base)
        task_executor = S.TaskExecutor(repo, max_parallel=1)
        task_executor._cleanup_worktree(wt_a)
        paths = [str(Path(p).resolve()) for p in _worktree_paths(repo)]
        assert str(wt_b.resolve()) in paths, paths
        assert wt_b.exists(), wt_b
        task_executor._cleanup_worktree(wt_b)
        _assert_only_main_worktree(repo)
    print("ok  cleanup of one worktree leaves a live sibling worktree intact")


def test_abort_branch_wave_does_not_cleanup_live_child_executors():
    class ChildExecutor:
        cleaned = False

        def _cleanup_active_resources(self):
            self.cleaned = True

    parent = S.TaskExecutor(max_parallel=1)
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = concurrent.futures.Future()
    child_executor = ChildExecutor()
    plan = S.PlannedBranchTask("live", "BASE0", "ai-org/tasks/root/live-123")
    parent._abort_branch_wave(
        executor,
        {future: (S.TaskNode("live", kind=S.LEAF, objective="live"), plan)},
        {future: child_executor},
    )
    assert future.cancelled(), "pending branch future should be cancelled"
    assert not child_executor.cleaned, "abort cleanup must not remove live sibling resources"
    print("ok  abort path does not cleanup live child executors' resources")


def test_parallel_child_abort_cleans_parent_registered_pgid():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _temp_git_repo(tmp)
        base = _git(repo, "rev-parse", "HEAD").strip()
        slow_ready = threading.Event()
        proc_holder: dict[str, subprocess.Popen] = {}
        executor_holder: dict[str, S.TaskExecutor] = {}

        def run_leaf(node):
            task_executor = executor_holder["executor"]
            if node.id == "fail":
                assert slow_ready.wait(5), "slow sibling did not register its resources"
                raise RuntimeError("abort the wave")

            wt = Path(tempfile.mkdtemp(prefix="t-abort-child-"))
            add = task_executor._git("worktree", "add", "--detach", str(wt), node.base_sha)
            assert add.returncode == 0, add.stderr
            task_executor._register_worktree(wt)
            proc = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            proc_holder["proc"] = proc
            task_executor.register_carrier_pgid(proc.pid)
            slow_ready.set()
            try:
                deadline = time.monotonic() + 5
                while proc.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                return S.VerifiedCommit(node.id, "0" * 40, {"kind": "leaf"})
            finally:
                task_executor.unregister_carrier_pgid(proc.pid)
                task_executor._cleanup_worktree(wt)

        root = S.TaskNode("root", kind=S.COMPOSITE, base_sha=base, objective="compose", subtasks=[
            S.TaskNode("slow", kind=S.LEAF, objective="long running"),
            S.TaskNode("fail", kind=S.LEAF, objective="fail fast"),
        ])
        task_executor = S.TaskExecutor(repo, run_leaf=run_leaf, max_parallel=2)
        executor_holder["executor"] = task_executor
        try:
            task_executor.execute(root)
            raise AssertionError("parallel child failure must raise")
        except RuntimeError as exc:
            assert "abort the wave" in str(exc), exc

        proc = proc_holder["proc"]
        deadline = time.monotonic() + 5
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert proc.poll() is not None, "abort cleanup must kill registered carrier pgid"
        _assert_only_main_worktree(repo)
    print("ok  parallel child abort cleans in-flight worktree and registered carrier pgid")


def test_cockpit_events_cover_start_done_split_and_empty_fallback():
    events: list[dict] = []

    def run_leaf(node):
        return S.VerifiedCommit(node.id, f"leaf-sha-{node.id}", {"kind": "leaf"})

    def verify(node, integrated_head, child_commits):
        return {"verified": True}

    def integrate(node, base, child_commits):
        return (f"integrated-head-{node.id}", None)

    def commit_integration(node, base, integrated_head, integ_wt, evidence):
        return f"integ-sha-{node.id}"

    split_root = S.TaskNode("root", kind=S.COMPOSITE, base_sha="BASE0", objective="compose")
    def split_once(node):
        if node.id == "root":
            return [S.TaskNode("leaf", kind=S.LEAF, base_sha=node.base_sha,
                               objective="do leaf", depth=node.depth + 1)]
        return []

    task_executor = S.TaskExecutor(run_leaf=run_leaf, verify=verify, integrate=integrate,
                      commit_integration=commit_integration, decomposer=split_once,
                      max_parallel=1, emit=events.append)
    task_executor.execute(split_root)

    assert any(e.get("type") == "leaf_start" and e.get("id") == "root" for e in events), events
    assert any(e.get("type") == "leaf_split" and e.get("id") == "root" and e.get("children") == ["leaf"]
               for e in events), events
    assert any(e.get("type") == "leaf_done" and e.get("id") == "leaf" and e.get("commit") == "leaf-sha-leaf"
               for e in events), events

    fallback_events: list[dict] = []
    fallback = S.TaskNode("fallback", kind=S.COMPOSITE, base_sha="BASE0", objective="atomic")
    task_executor = S.TaskExecutor(run_leaf=run_leaf, verify=verify, integrate=integrate,
                      commit_integration=commit_integration, decomposer=lambda _node: [],
                      max_parallel=1, emit=fallback_events.append)
    task_executor.execute(fallback)
    assert any(e.get("type") == "decompose_empty_fallback" and e.get("id") == "fallback"
               for e in fallback_events), fallback_events
    print("ok  cockpit events: leaf_start, leaf_split, leaf_done(commit), decompose_empty_fallback")


def test_tree_forbidden_patterns_aggregate_through_composite_evidence():
    pattern = {"pattern": "TREE_TOKEN", "scope": "tree", "reason": "goal-wide rename"}

    def run_leaf(node):
        if node.id == "A":
            evidence = {"kind": "leaf", "tree_forbidden_patterns": [pattern]}
        else:
            evidence = {"kind": "leaf", "aufheben": {"forbidden_patterns": [pattern, {"pattern": "LOCAL"}]}}
        return S.VerifiedCommit(node.id, f"leaf-sha-{node.id}", evidence)

    def verify(node, integrated_head, child_commits):
        return {"verified": True}

    def integrate(node, base, child_commits):
        return (f"integrated-head-{node.id}", None)

    def commit_integration(node, base, integrated_head, integ_wt, evidence):
        return f"integ-sha-{node.id}"

    root = S.TaskNode("root", kind=S.COMPOSITE, base_sha="BASE0", objective="compose", subtasks=[
        S.TaskNode("A", kind=S.LEAF, objective="do A"),
        S.TaskNode("B", kind=S.LEAF, objective="do B"),
    ])
    task_executor = S.TaskExecutor(run_leaf=run_leaf, verify=verify, integrate=integrate,
                      commit_integration=commit_integration, max_parallel=1)
    result = task_executor.execute(root)

    patterns = result.evidence["tree_forbidden_patterns"]
    assert patterns == [pattern], patterns
    print("ok  tree-scoped forbidden patterns aggregate once through composite evidence")


if __name__ == "__main__":
    test_true_recursion_commit_per_node()
    test_real_git_integration()
    test_parallel_independent_tasks_integrate_as_isolated_branches()
    test_serial_child_inherits_dependency_output_commit()
    test_dependency_forces_branch_base_and_integration_order()
    test_duplicate_sibling_task_ids_fail_closed()
    test_dependency_cycle_fails_closed()
    test_depends_on_unknown_sibling_id_fails_closed()
    test_depends_on_cross_level_id_fails_closed()
    test_serial_child_with_multiple_deps_resumes_from_integrated_head()
    test_multi_dep_base_preintegration_uses_topo_order_not_literal_order()
    test_nested_parallel_composites_complete_under_timeout()
    test_default_max_depth_is_floor_when_env_unset()
    test_root_decompose_result_sets_max_depth_once()
    test_failing_composite_verify_blocks_integration_commit()
    test_cherry_pick_conflict_is_detected_and_aborts_integration()
    test_semantic_conflict_is_caught_by_integration_verifier()
    test_mutable_child_base_ref_is_rejected()
    test_rerun_child_base_uses_current_parent_base_not_stale_child_base()
    test_inconsistent_inherited_child_base_fails_closed()
    test_failed_task_branch_ref_is_deleted()
    test_redundant_non_empty_child_fails_closed()
    test_genuinely_empty_child_is_recorded_and_allowed()
    test_planned_unpublished_task_ref_is_deleted_on_abort()
    test_executor_model_has_no_shared_lock_across_blocking_wait()
    test_default_leaf_exception_leaves_no_worktree()
    test_composite_verify_failure_leaves_no_integration_worktree()
    test_worktree_cleanup_is_idempotent()
    test_worktree_cleanup_leaves_live_sibling_worktree_intact()
    test_abort_branch_wave_does_not_cleanup_live_child_executors()
    test_parallel_child_abort_cleans_parent_registered_pgid()
    test_cockpit_events_cover_start_done_split_and_empty_fallback()
    test_tree_forbidden_patterns_aggregate_through_composite_evidence()
    print("\nall task_executor tests passed.")
