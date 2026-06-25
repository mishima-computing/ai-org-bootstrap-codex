#!/usr/bin/env python3
"""Tests for scripts/deadlock_static_risk.py.

Plain assert + __main__, matching the scripts/ test idiom.
"""
from __future__ import annotations

import sys
import tempfile
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deadlock_static_risk as gate  # noqa: E402


def _scan(src: str):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "case.py"
        p.write_text(textwrap.dedent(src).lstrip(), encoding="utf-8")
        return gate.analyze_path(p)


def _fails(src: str):
    return [f for f in _scan(src) if f.severity == "FAIL"]


def _advisories(src: str):
    return [f for f in _scan(src) if f.severity == "ADVISORY"]


def test_subprocess_run_under_lock_is_fail_line():
    findings = _fails("""
        import subprocess, threading
        lock = threading.Lock()
        def f():
            with lock:
                subprocess.run(["sleep", "1"])
    """)
    assert len(findings) == 1, findings
    assert findings[0].line == 5 and "subprocess.run" in findings[0].message, findings
    print("ok  subprocess.run under lock is FAIL at blocking-call line")


def test_future_result_under_lock_is_fail_line():
    findings = _fails("""
        import threading
        lock = threading.RLock()
        def f(fut):
            with lock:
                fut.result()
    """)
    assert len(findings) == 1, findings
    assert findings[0].line == 5 and ".result" in findings[0].message, findings
    print("ok  Future.result under lock is FAIL at blocking-call line")


def test_join_under_acquire_release_is_fail_line():
    findings = _fails("""
        import threading
        lock = threading.Lock()
        t = threading.Thread(target=lambda: None)
        def f():
            lock.acquire()
            t.join()
            lock.release()
    """)
    assert len(findings) == 1, findings
    assert findings[0].line == 6 and ".join" in findings[0].message, findings
    print("ok  join under acquire/release lock is FAIL at blocking-call line")


def test_queue_get_under_lock_is_fail_line():
    findings = _fails("""
        import queue, threading
        lock = threading.Lock()
        q = queue.Queue()
        def f():
            with lock:
                q.get()
    """)
    assert len(findings) == 1, findings
    assert findings[0].line == 6 and "q.get" in findings[0].message, findings
    print("ok  queue.get under lock is FAIL at blocking-call line")


def test_timeout_exempts_blocking_calls():
    findings = _fails("""
        import concurrent.futures, queue, subprocess, threading
        lock = threading.Lock()
        q = queue.Queue()
        def f(fut, t):
            with lock:
                subprocess.run(["sleep", "1"], timeout=1)
                fut.result(timeout=1)
                t.join(timeout=1)
                q.get(timeout=1)
    """)
    assert findings == [], findings
    print("ok  finite timeout exempts subprocess/Future/join/queue waits")


def test_blocking_outside_lock_is_not_flagged():
    findings = _fails("""
        import subprocess, threading
        lock = threading.Lock()
        def f():
            subprocess.run(["sleep", "1"])
            with lock:
                x = 1
            return x
    """)
    assert findings == [], findings
    print("ok  blocking call outside any lock is not flagged")


def test_local_suppression_on_blocking_call():
    findings = _fails("""
        import subprocess, threading
        lock = threading.Lock()
        def f():
            with lock:
                subprocess.run(["sleep", "1"])  # aob-deadlock-ok: child is guaranteed bounded externally
    """)
    assert findings == [], findings
    print("ok  local suppression on blocking call suppresses finding")


def test_local_suppression_on_lock_region():
    findings = _fails("""
        import subprocess, threading
        lock = threading.Lock()
        def f():
            with lock:  # aob-deadlock-ok: synthetic fixture documents suppression
                subprocess.run(["sleep", "1"])
    """)
    assert findings == [], findings
    print("ok  local suppression on lock line suppresses held-region finding")


def test_lock_order_cycle_is_advisory_only():
    findings = _scan("""
        import threading
        a = threading.Lock()
        b = threading.Lock()
        def one():
            with a:
                with b:
                    pass
        def two():
            with b:
                with a:
                    pass
    """)
    advisories = [f for f in findings if f.code == "AOB-DEADLOCK-ORDER"]
    assert advisories, findings
    assert not any(f.severity == "FAIL" for f in findings), findings
    print("ok  A->B / B->A lock-order cycle is ADVISORY only")


def test_executor_self_method_wait_reaching_class_lock_is_fail():
    findings = _fails("""
        import concurrent.futures, threading
        class Executor:
            def __init__(self):
                self._lock = threading.Lock()
            def execute(self, node):
                with self._lock:
                    self.calls.append(node)
                return self.run_leaf(node)
            def run_leaf(self, node):
                return node
            def fanout(self, nodes):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [executor.submit(self.execute, n) for n in nodes]
                    for future in concurrent.futures.as_completed(futures):
                        future.result()
    """)
    assert any(f.code == "AOB-DEADLOCK-002" and f.line in {15, 16} for f in findings), findings
    print("ok  executor wait over self method that reaches class lock is FAIL")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} passed")
