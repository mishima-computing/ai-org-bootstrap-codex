#!/usr/bin/env python3
"""Deterministically scaffold implementation and test skeletons from a frozen .pyi interface."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ast
import copy
import importlib.util
import tempfile


def _read_source(path) -> str:
    return Path(path).read_text(encoding="utf-8")


def _write_source(path, source: str) -> int:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = source.encode("utf-8")
    target.write_bytes(data)
    return len(data)


def _source_segment(source: str, node: ast.AST) -> str:
    segment = ast.get_source_segment(source, node)
    if segment is None:
        return ast.unparse(node)
    return segment


def _docstring_node(text: str) -> ast.Expr:
    return ast.Expr(value=ast.Constant(value=text))


def _raise_node(name: str) -> ast.Raise:
    return ast.Raise(
        exc=ast.Call(
            func=ast.Name(id="NotImplementedError", ctx=ast.Load()),
            args=[ast.Constant(value=name)],
            keywords=[],
        ),
        cause=None,
    )


def _pass_node() -> ast.Pass:
    return ast.Pass()


def _function_scaffold(node: ast.FunctionDef | ast.AsyncFunctionDef, error_name: str):
    scaffold = copy.deepcopy(node)
    docstring = ast.get_docstring(node, clean=False)
    if docstring is None:
        docstring = f"TODO: implement {node.name}."
    scaffold.body = [_docstring_node(docstring), _raise_node(error_name)]
    ast.fix_missing_locations(scaffold)
    return ast.unparse(scaffold)


def _class_scaffold(node: ast.ClassDef) -> str:
    scaffold = copy.deepcopy(node)
    docstring = ast.get_docstring(node, clean=False)
    body: list[ast.stmt] = []
    if docstring is not None:
        body.append(_docstring_node(docstring))

    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            method = copy.deepcopy(item)
            method_docstring = ast.get_docstring(item, clean=False)
            if method_docstring is None:
                method_docstring = f"TODO: implement {item.name}."
            method.body = [
                _docstring_node(method_docstring),
                _raise_node(f"{node.name}.{item.name}"),
            ]
            body.append(method)

    if not any(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) for item in node.body):
        body.append(_pass_node())

    if not body:
        body.append(_pass_node())

    scaffold.body = body
    ast.fix_missing_locations(scaffold)
    return ast.unparse(scaffold)


def _top_level_symbols(tree: ast.Module) -> list[str]:
    symbols: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.append(node.name)
    return symbols


def scaffold_module(interface_pyi, target_py) -> dict:
    """Generate a module skeleton from a .pyi interface."""
    source = _read_source(interface_pyi)
    tree = ast.parse(source, filename=str(interface_pyi))

    chunks: list[str] = []
    symbols: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            chunks.append(_source_segment(source, node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chunks.append(_function_scaffold(node, node.name))
            symbols.append(node.name)
        elif isinstance(node, ast.ClassDef):
            chunks.append(_class_scaffold(node))
            symbols.append(node.name)

    output = "\n\n".join(chunks)
    if output:
        output += "\n"
    byte_count = _write_source(target_py, output)
    return {"target": str(target_py), "symbols": symbols, "bytes": byte_count}


def scaffold_tests(interface_pyi, target_test_py, import_path) -> dict:
    """Generate placeholder existence tests for public top-level symbols."""
    source = _read_source(interface_pyi)
    tree = ast.parse(source, filename=str(interface_pyi))

    tests: list[str] = []
    chunks = [f"import {import_path} as _mod"]
    for name in _top_level_symbols(tree):
        if name.startswith("_"):
            continue
        test_name = f"test_{name}_exists"
        tests.append(test_name)
        chunks.append(f'def {test_name}():\n    assert hasattr(_mod, "{name}")')

    output = "\n\n".join(chunks) + "\n"
    _write_source(target_test_py, output)
    return {"target": str(target_test_py), "tests": tests}


def _import_from_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _self_test() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        stub = root / "sample.pyi"
        module = root / "sample.py"
        test_module = root / "test_sample.py"
        stub.write_text(
            'from typing import Iterable\n\n'
            'def add(a: int, b: int) -> int:\n'
            '    """Add two integers."""\n'
            '    ...\n\n'
            'class Worker:\n'
            '    def run(self, items: Iterable[int]) -> list[int]: ...\n',
            encoding="utf-8",
        )

        scaffold_module(stub, module)
        scaffold_tests(stub, test_module, "sample")

        module_source = module.read_text(encoding="utf-8")
        ast.parse(module_source)
        assert "def add(a: int, b: int) -> int:" in module_source
        assert "def run(self, items: Iterable[int]) -> list[int]:" in module_source
        assert "raise NotImplementedError" in module_source

        _import_from_path("sample", module)
        sys.path.insert(0, str(root))
        try:
            test_source = test_module.read_text(encoding="utf-8")
            ast.parse(test_source)
            _import_from_path("test_sample", test_module)
        finally:
            sys.path.remove(str(root))


if __name__ == "__main__":
    _self_test()
    print("controller_scaffold self-test ok")
