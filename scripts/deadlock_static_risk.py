#!/usr/bin/env python3
"""Static deadlock-risk gate for lock-held blocking waits.

This is a pure-stdlib AST analyzer. It flags the high-confidence pattern this
engine must keep out of its controller paths: a known lock held lexically across
a blocking wait that has no finite timeout. It scans whole files, not diffs.

It is deliberately a risk detector, not a proof of deadlock. It cannot reliably
see dynamic aliases, locks stored in containers, callbacks invoked by blocking
APIs, cross-process coordination, OS pipe-buffer backpressure, or data-dependent
lock ordering. Python's dynamic dispatch also means some receiver types are
inferred by name or assignment only. Findings should be treated as auditable
static evidence of dangerous structure, not as an exhaustive concurrency proof.

Usage:
  python3 scripts/deadlock_static_risk.py <files-or-dirs>
"""
from __future__ import annotations

import argparse
import ast
import dataclasses
import sys
import tokenize
from pathlib import Path
from typing import Iterable

SUPPRESS = "aob-deadlock-ok:"

LOCK_FACTORIES = {
    "threading.Lock",
    "threading.RLock",
    "threading.Condition",
    "multiprocessing.Lock",
    "multiprocessing.RLock",
}

POOL_FACTORIES = {
    "concurrent.futures.ThreadPoolExecutor",
    "concurrent.futures.ProcessPoolExecutor",
    "ThreadPoolExecutor",
    "ProcessPoolExecutor",
}

THREAD_FACTORIES = {
    "threading.Thread",
    "multiprocessing.Process",
    "Thread",
    "Process",
}

QUEUE_FACTORIES = {"queue.Queue", "Queue"}
POPEN_FACTORIES = {"subprocess.Popen", "Popen"}


@dataclasses.dataclass(frozen=True)
class Finding:
    severity: str
    path: str
    line: int
    col: int
    code: str
    message: str

    def render(self) -> str:
        loc = f"{self.path}:{self.line}:{self.col + 1}"
        return f"{loc}: {self.severity} {self.code}: {self.message}"


@dataclasses.dataclass(frozen=True)
class HeldLock:
    name: str
    line: int
    suppressed: bool = False


@dataclasses.dataclass(frozen=True)
class BlockingCall:
    name: str
    no_timeout: bool


class FileAnalyzer:
    def __init__(self, path: Path, source: str):
        self.path = str(path)
        self.source = source
        self.comments = _comments_by_line(source)
        self.tree = ast.parse(source, filename=self.path)
        self.lock_names: set[str] = set()
        self.popen_names: set[str] = set()
        self.thread_names: set[str] = set()
        self.queue_names: set[str] = set()
        self.executor_names: set[str] = set()
        self.context_by_func: dict[ast.AST, set[str]] = {}
        self.edges: dict[tuple[str, str], list[tuple[int, str]]] = {}
        self.findings: list[Finding] = []
        self._collect_symbols_and_context()

    def analyze(self) -> list[Finding]:
        self._scan_block(self.tree.body, [], "<module>")
        self._report_executor_self_lock_waits()
        self._report_lock_order_cycles()
        return sorted(self.findings, key=lambda f: (f.path, f.line, f.col, f.severity, f.code))

    # -- first pass --------------------------------------------------------------------------------
    def _collect_symbols_and_context(self) -> None:
        parent_stack: list[ast.AST] = []

        class Visitor(ast.NodeVisitor):
            def visit_FunctionDef(vself, node: ast.FunctionDef) -> None:
                parent_stack.append(node)
                vself.generic_visit(node)
                parent_stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Assign(vself, node: ast.Assign) -> None:
                self._record_assignment(node.targets, node.value)
                vself.generic_visit(node)

            def visit_AnnAssign(vself, node: ast.AnnAssign) -> None:
                if node.value is not None:
                    self._record_assignment([node.target], node.value)
                vself.generic_visit(node)

            def visit_Call(vself, node: ast.Call) -> None:
                if parent_stack:
                    contexts = self.context_by_func.setdefault(parent_stack[-1], set())
                    fullname = _call_fullname(node)
                    attr = node.func.attr if isinstance(node.func, ast.Attribute) else ""
                    if fullname in POOL_FACTORIES:
                        contexts.add("uses executor")
                    if fullname in POPEN_FACTORIES or fullname in {
                        "subprocess.run", "subprocess.call", "subprocess.check_call",
                        "subprocess.check_output",
                    }:
                        contexts.add("spawns subprocess/carrier")
                    if attr in {"write_text", "replace", "update", "record_session", "save_wip",
                                "save_done", "append", "add", "discard"}:
                        contexts.add("mutates shared run-state")
                vself.generic_visit(node)

        Visitor().visit(self.tree)

    def _record_assignment(self, targets: list[ast.AST], value: ast.AST) -> None:
        if not isinstance(value, ast.Call):
            return
        fullname = _call_fullname(value)
        dests = [_expr_name(target) for target in targets]
        for dest in dests:
            if not dest:
                continue
            if fullname in LOCK_FACTORIES:
                self.lock_names.add(dest)
            elif fullname in POPEN_FACTORIES:
                self.popen_names.add(dest)
            elif fullname in THREAD_FACTORIES:
                self.thread_names.add(dest)
            elif fullname in QUEUE_FACTORIES:
                self.queue_names.add(dest)
            elif fullname in POOL_FACTORIES:
                self.executor_names.add(dest)

    # -- statement walk ----------------------------------------------------------------------------
    def _scan_block(self, statements: list[ast.stmt], held: list[HeldLock], func_name: str) -> None:
        i = 0
        while i < len(statements):
            stmt = statements[i]
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self._scan_block(stmt.body, [], stmt.name)
                elif isinstance(stmt, ast.ClassDef):
                    self._scan_block(stmt.body, [], stmt.name)
                i += 1
                continue

            acquire = self._acquire_lock_from_stmt(stmt)
            if acquire is not None:
                lock_name = acquire
                suppressed = self._suppressed(stmt.lineno)
                release_index = self._matching_release_index(statements, i + 1, lock_name)
                end = release_index if release_index is not None else len(statements)
                acquired = HeldLock(lock_name, stmt.lineno, suppressed)
                self._record_lock_edges(held, acquired, func_name)
                self._scan_block(statements[i + 1:end], held + [acquired], func_name)
                if release_index is not None:
                    i = release_index + 1
                else:
                    i = end
                continue

            self._scan_statement(stmt, held, func_name)
            i += 1

    def _scan_statement(self, stmt: ast.stmt, held: list[HeldLock], func_name: str) -> None:
        if isinstance(stmt, ast.With):
            self._scan_with(stmt, held, func_name)
            return
        if isinstance(stmt, ast.AsyncWith):
            self._scan_with(stmt, held, func_name)
            return

        self._scan_calls_in_node(stmt, held, func_name)

        for child_body in _child_statement_bodies(stmt):
            self._scan_block(child_body, held, func_name)

    def _scan_with(self, stmt: ast.With | ast.AsyncWith, held: list[HeldLock], func_name: str) -> None:
        current = list(held)
        for item in stmt.items:
            expr = item.context_expr
            lock_name = self._lock_expr_name(expr)
            if lock_name:
                acquired = HeldLock(lock_name, getattr(expr, "lineno", stmt.lineno),
                                    self._suppressed(getattr(expr, "lineno", stmt.lineno)))
                self._record_lock_edges(current, acquired, func_name)
                current.append(acquired)
            else:
                self._scan_calls_in_node(expr, current, func_name)
        self._scan_block(stmt.body, current, func_name)

    def _scan_calls_in_node(self, node: ast.AST, held: list[HeldLock], func_name: str) -> None:
        for call in [n for n in ast.walk(node) if isinstance(n, ast.Call)]:
            blocking = self._blocking_call(call)
            if not blocking or not blocking.no_timeout:
                continue
            if not held:
                continue
            if self._suppressed(call.lineno) or any(lock.suppressed for lock in held):
                continue
            locks = ", ".join(lock.name for lock in held)
            line = call.lineno
            context = self._risk_context(call)
            suffix = f" ({'; '.join(sorted(context))})" if context else ""
            self.findings.append(Finding(
                "FAIL",
                self.path,
                line,
                call.col_offset,
                "AOB-DEADLOCK-001",
                f"{blocking.name} without a finite timeout while holding {locks}{suffix}",
            ))

    # -- lock recognition --------------------------------------------------------------------------
    def _lock_expr_name(self, expr: ast.AST) -> str | None:
        name = _expr_name(expr)
        if not name:
            return None
        if name in self.lock_names:
            return name
        leaf = name.rsplit(".", 1)[-1].lower()
        if leaf in {"lock", "rlock", "condition", "_lock"} or leaf.endswith("_lock"):
            return name
        return None

    def _acquire_lock_from_stmt(self, stmt: ast.stmt) -> str | None:
        if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
            return None
        call = stmt.value
        if not isinstance(call.func, ast.Attribute) or call.func.attr != "acquire":
            return None
        return self._lock_expr_name(call.func.value)

    def _release_lock_from_stmt(self, stmt: ast.stmt, lock_name: str) -> bool:
        if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
            return False
        call = stmt.value
        if not isinstance(call.func, ast.Attribute) or call.func.attr != "release":
            return False
        return _expr_name(call.func.value) == lock_name

    def _matching_release_index(self, statements: list[ast.stmt], start: int, lock_name: str) -> int | None:
        depth = 1
        for i in range(start, len(statements)):
            stmt = statements[i]
            if self._acquire_lock_from_stmt(stmt) == lock_name:
                depth += 1
            if self._release_lock_from_stmt(stmt, lock_name):
                depth -= 1
                if depth == 0:
                    return i
        return None

    # -- blocking call recognition -----------------------------------------------------------------
    def _blocking_call(self, call: ast.Call) -> BlockingCall | None:
        fullname = _call_fullname(call)
        if fullname in {"subprocess.run", "subprocess.call", "subprocess.check_call",
                        "subprocess.check_output"}:
            return BlockingCall(fullname, not _has_finite_timeout(call, positional_indexes=()))

        if fullname in {"concurrent.futures.wait", "concurrent.futures.as_completed", "wait",
                        "as_completed"}:
            return BlockingCall(fullname, not _has_finite_timeout(call, positional_indexes=(1,)))

        if isinstance(call.func, ast.Attribute):
            recv = _expr_name(call.func.value) or ""
            method = call.func.attr
            if method in {"wait", "communicate"} and (recv in self.popen_names or recv.lower() in {"proc", "p"}):
                return BlockingCall(f"{recv}.{method}", not _has_finite_timeout(call, positional_indexes=(0,)))
            if method == "result":
                return BlockingCall(f"{recv}.result", not _has_finite_timeout(call, positional_indexes=(0,)))
            if method == "map" and (recv in self.executor_names or recv.lower() in {"executor", "ex"}):
                return BlockingCall(f"{recv}.map", not _has_finite_timeout(call, positional_indexes=(2,)))
            if method == "join":
                if recv in self.queue_names or recv.lower() in {"q", "queue"}:
                    return BlockingCall(f"{recv}.join", True)
                return BlockingCall(f"{recv}.join", not _has_finite_timeout(call, positional_indexes=(0,)))
            if method in {"get", "put"} and (recv in self.queue_names or recv.lower() in {"q", "queue"}):
                return BlockingCall(f"{recv}.{method}", not _has_finite_timeout(call, positional_indexes=(1,)))
        return None

    def _risk_context(self, call: ast.Call) -> set[str]:
        context: set[str] = set()
        owner = _nearest_function(self.tree, call)
        if owner is not None:
            context.update(self.context_by_func.get(owner, set()))
        for node in ast.walk(owner or self.tree):
            if isinstance(node, ast.Call):
                fullname = _call_fullname(node)
                if fullname in POOL_FACTORIES:
                    context.add("escalated: executor in same region")
                if fullname in POPEN_FACTORIES or fullname in {
                    "subprocess.run", "subprocess.call", "subprocess.check_call", "subprocess.check_output",
                }:
                    context.add("escalated: subprocess/carrier in same region")
        return context

    # -- advisory lock ordering --------------------------------------------------------------------
    def _record_lock_edges(self, held: list[HeldLock], acquired: HeldLock, func_name: str) -> None:
        if acquired.suppressed:
            return
        for outer in held:
            if outer.suppressed or outer.name == acquired.name:
                continue
            self.edges.setdefault((outer.name, acquired.name), []).append((acquired.line, func_name))

    def _report_lock_order_cycles(self) -> None:
        for (left, right), sites in sorted(self.edges.items()):
            reverse = self.edges.get((right, left))
            if not reverse:
                continue
            line, func = sites[0]
            rline, rfunc = reverse[0]
            if (left, right) > (right, left):
                continue
            self.findings.append(Finding(
                "ADVISORY",
                self.path,
                line,
                0,
                "AOB-DEADLOCK-ORDER",
                f"possible lock-order cycle {left} -> {right} in {func} and "
                f"{right} -> {left} in {rfunc} at line {rline}",
            ))

    def _report_executor_self_lock_waits(self) -> None:
        """Catch the controller executor shape that is not lexical in one function.

        A worker method is submitted to a ThreadPoolExecutor, the submitted method
        path acquires ``self._lock``, and the dispatcher waits indefinitely for
        futures. The lock is held/acquired in the submitted path rather than in
        the waiter's immediate lexical block, so the first pass intentionally
        does not see it; this supplement keeps the gate useful for the engine's
        actual carrier-orchestration deadlock class.
        """
        for cls in [n for n in ast.walk(self.tree) if isinstance(n, ast.ClassDef)]:
            methods = {
                node.name: node for node in cls.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            lock_methods = {
                name for name, node in methods.items()
                if self._method_acquires_self_lock(node)
            }
            if not lock_methods:
                continue
            for method_name, method in methods.items():
                submitted = self._self_methods_submitted_to_executor(method)
                if not submitted:
                    continue
                risky = sorted(name for name in submitted if self._method_reaches_lock(name, methods, lock_methods))
                if not risky:
                    continue
                for call in self._unbounded_future_waits(method):
                    if self._suppressed(call.lineno):
                        continue
                    context = self._class_risk_context(cls)
                    suffix = f" ({'; '.join(sorted(context))})" if context else ""
                    self.findings.append(Finding(
                        "FAIL",
                        self.path,
                        call.lineno,
                        call.col_offset,
                        "AOB-DEADLOCK-002",
                        "unbounded Future wait over executor-submitted self method(s) "
                        f"{', '.join(risky)} whose path acquires self._lock{suffix}",
                    ))

    def _method_acquires_self_lock(self, method: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        for node in ast.walk(method):
            if isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if _expr_name(item.context_expr) in {"self._lock", "self.lock"}:
                        return True
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "acquire" and _expr_name(node.func.value) in {"self._lock", "self.lock"}:
                    return True
        return False

    def _self_methods_submitted_to_executor(self, method: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
        submitted: set[str] = set()
        for node in ast.walk(method):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "submit":
                continue
            recv = _expr_name(node.func.value) or ""
            if recv.lower() not in {"executor", "ex"} and recv not in self.executor_names:
                continue
            if not node.args:
                continue
            target = node.args[0]
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
                submitted.add(target.attr)
        return submitted

    def _method_reaches_lock(
        self,
        method_name: str,
        methods: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
        lock_methods: set[str],
    ) -> bool:
        seen: set[str] = set()

        def walk(name: str) -> bool:
            if name in seen:
                return False
            seen.add(name)
            if name in lock_methods:
                return True
            node = methods.get(name)
            if node is None:
                return False
            for call in [n for n in ast.walk(node) if isinstance(n, ast.Call)]:
                if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) \
                        and call.func.value.id == "self":
                    if walk(call.func.attr):
                        return True
            return False

        return walk(method_name)

    def _unbounded_future_waits(self, method: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
        waits: list[ast.Call] = []
        for node in ast.walk(method):
            if not isinstance(node, ast.Call):
                continue
            blocking = self._blocking_call(node)
            if not blocking:
                continue
            if blocking.name.endswith(".result") or blocking.name in {"concurrent.futures.as_completed",
                                                                       "as_completed",
                                                                       "concurrent.futures.wait", "wait"}:
                if blocking.no_timeout:
                    waits.append(node)
        return waits

    def _class_risk_context(self, cls: ast.ClassDef) -> set[str]:
        context: set[str] = {"escalated: executor wait crosses class lock"}
        text = ast.get_source_segment(self.source, cls) or ""
        if "carrier" in text or "_run_leaf" in text:
            context.add("escalated: carrier orchestration in class")
        if "subprocess" in text or "_git(" in text:
            context.add("escalated: subprocess/git work in class")
        return context

    def _suppressed(self, line: int) -> bool:
        return SUPPRESS in self.comments.get(line, "")


def _comments_by_line(source: str) -> dict[int, str]:
    comments: dict[int, str] = {}
    try:
        tokens = tokenize.generate_tokens(iter(source.splitlines(True)).__next__)
        for tok in tokens:
            if tok.type == tokenize.COMMENT:
                comments[tok.start[0]] = tok.string
    except tokenize.TokenError:
        return comments
    return comments


def _expr_name(expr: ast.AST) -> str | None:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        base = _expr_name(expr.value)
        return f"{base}.{expr.attr}" if base else expr.attr
    if isinstance(expr, ast.Subscript):
        return _expr_name(expr.value)
    if isinstance(expr, (ast.Tuple, ast.List)):
        names = [_expr_name(e) for e in expr.elts]
        return names[0] if names and names[0] else None
    return None


def _call_fullname(call: ast.Call) -> str:
    return _expr_name(call.func) or ""


def _has_finite_timeout(call: ast.Call, positional_indexes: tuple[int, ...]) -> bool:
    for kw in call.keywords:
        if kw.arg == "timeout" and _finite_timeout_value(kw.value):
            return True
    for index in positional_indexes:
        if index < len(call.args) and _finite_timeout_value(call.args[index]):
            return True
    return False


def _finite_timeout_value(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        if node.value is None:
            return False
        if isinstance(node.value, (int, float)):
            return node.value > 0
        return True
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
        if isinstance(node.operand.value, (int, float)):
            return False
    return False


def _child_statement_bodies(stmt: ast.stmt) -> list[list[ast.stmt]]:
    bodies: list[list[ast.stmt]] = []
    for attr in ("body", "orelse", "finalbody"):
        value = getattr(stmt, attr, None)
        if isinstance(value, list):
            bodies.append(value)
    handlers = getattr(stmt, "handlers", None)
    if handlers:
        bodies.extend(handler.body for handler in handlers)
    return bodies


def _nearest_function(root: ast.AST, target: ast.AST) -> ast.AST | None:
    best: ast.AST | None = None
    best_span = 10**12
    target_line = getattr(target, "lineno", -1)
    for node in ast.walk(root):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start = getattr(node, "lineno", -1)
        end = getattr(node, "end_lineno", start)
        if start <= target_line <= end and end - start < best_span:
            best = node
            best_span = end - start
    return best


def analyze_path(path: Path) -> list[Finding]:
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        source = path.read_text(encoding="utf-8", errors="replace")
    try:
        return FileAnalyzer(path, source).analyze()
    except SyntaxError as exc:
        return [Finding("ADVISORY", str(path), exc.lineno or 1, exc.offset or 0,
                        "AOB-DEADLOCK-SYNTAX", f"skipped unparsable Python: {exc.msg}")]


def iter_python_files(args: Iterable[str]) -> list[Path]:
    files: list[Path] = []
    for raw in args:
        path = Path(raw)
        if path.is_dir():
            files.extend(sorted(p for p in path.rglob("*.py") if p.is_file()))
        elif path.is_file():
            files.append(path)
    return sorted(dict.fromkeys(files))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Static gate for lock-held blocking waits without timeout.")
    parser.add_argument("paths", nargs="+", help="Python files or directories to scan")
    args = parser.parse_args(argv)
    findings: list[Finding] = []
    for path in iter_python_files(args.paths):
        findings.extend(analyze_path(path))
    for finding in sorted(findings, key=lambda f: (f.path, f.line, f.col, f.severity, f.code)):
        print(finding.render())
    return 1 if any(f.severity == "FAIL" for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
