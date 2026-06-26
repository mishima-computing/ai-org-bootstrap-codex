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
                           objective="alpha area work", depth=node.depth + 1),
                S.TaskNode("root.sibling", kind=S.COMPOSITE, base_sha=node.base_sha,
                           objective="beta area work", depth=node.depth + 1),
            ], max_depth=2)
        return S.DecomposeResult([
            S.TaskNode(f"{node.id}.child", kind=S.COMPOSITE, base_sha=node.base_sha,
                       objective=f"{node.id} implementation", depth=node.depth + 1),
            S.TaskNode(f"{node.id}.sibling", kind=S.COMPOSITE, base_sha=node.base_sha,
                       objective=f"{node.id} verification", depth=node.depth + 1),
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
    assert sorted(leaves_executed) == [
        ("root.child.child", 2),
        ("root.child.sibling", 2),
        ("root.sibling.child", 2),
        ("root.sibling.sibling", 2),
    ], leaves_executed
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


def test_parallel_child_abort_cleans_inflight_worktree_and_pgid():
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
            return [
                S.TaskNode("leaf", kind=S.LEAF, base_sha=node.base_sha,
                           objective="do leaf", depth=node.depth + 1),
                S.TaskNode("other", kind=S.LEAF, base_sha=node.base_sha,
                           objective="do other", depth=node.depth + 1),
            ]
        return []

    task_executor = S.TaskExecutor(run_leaf=run_leaf, verify=verify, integrate=integrate,
                      commit_integration=commit_integration, decomposer=split_once,
                      max_parallel=1, emit=events.append)
    task_executor.execute(split_root)

    assert any(e.get("type") == "leaf_start" and e.get("id") == "root" for e in events), events
    assert any(e.get("type") == "leaf_split" and e.get("id") == "root" and e.get("children") == ["leaf", "other"]
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


def test_null_split_guard_rejects_two_child_whole_parent_restated_scope():
    events: list[dict] = []
    leaves: list[str] = []

    parent = S.TaskNode(
        "rename",
        kind=S.COMPOSITE,
        base_sha="BASE0",
        objective="rename cockpit/scaffold.py scaffold_runner SHAGIRI_SCAFFOLD to demo org while preserving real scaffold",
    )

    def decomposer(node):
        return [
            S.TaskNode(
                "rename-code",
                kind=S.COMPOSITE,
                base_sha=node.base_sha,
                objective=node.objective,
                depth=node.depth + 1,
            ),
            S.TaskNode(
                "rename-docs",
                kind=S.COMPOSITE,
                base_sha=node.base_sha,
                objective="update small docs mention after the rename",
                depth=node.depth + 1,
            ),
        ]

    def run_leaf(node):
        leaves.append(node.id)
        return S.VerifiedCommit(node.id, f"leaf-sha-{node.id}", {"kind": "leaf"})

    task_executor = S.TaskExecutor(run_leaf=run_leaf, decomposer=decomposer, max_parallel=1,
                                   max_depth=5, emit=events.append)
    result = task_executor.execute(parent)

    assert result.task_id == "rename", result
    assert leaves == ["rename"], leaves
    assert any(e.get("type") == "decompose_rejected" and e.get("id") == "rename"
               and "restates" in e.get("reason", "") for e in events), events
    print("ok  null-split guard rejects n=2 decomposition with a child equal to the parent scope")


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
    test_serial_child_inherits_dependency_output_commit()
    test_serial_child_with_multiple_deps_resumes_from_integrated_head()
    test_nested_parallel_composites_complete_under_timeout()
    test_default_max_depth_is_floor_when_env_unset()
    test_root_decompose_result_sets_max_depth_once()
    test_failing_composite_verify_blocks_integration_commit()
    test_default_leaf_exception_leaves_no_worktree()
    test_composite_verify_failure_leaves_no_integration_worktree()
    test_worktree_cleanup_is_idempotent()
    test_parallel_child_abort_cleans_inflight_worktree_and_pgid()
    test_cockpit_events_cover_start_done_split_and_empty_fallback()
    test_tree_forbidden_patterns_aggregate_through_composite_evidence()
    print("\nall task_executor tests passed.")
