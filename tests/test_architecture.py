from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "ai_org"

PHASES = {
    "ai_org.rfc": 0,
    "ai_org.patch": 1,
    "ai_org.merge": 2,
}


def test_ai_org_import_graph_is_acyclic_and_respects_boundaries():
    modules = _package_modules()
    graph = {module: _module_imports(path, module, modules) for module, path in modules.items()}

    cycle = _find_cycle(graph)
    assert cycle is None, "ai_org import graph has a cycle: " + " -> ".join(cycle or [])

    platform_domain_imports = [
        (source, target)
        for source, targets in graph.items()
        if source.startswith("ai_org.platform")
        for target in targets
        if _phase(target) is not None
    ]
    assert platform_domain_imports == []

    domain_back_edges = [
        (source, target)
        for source, targets in graph.items()
        for target in targets
        if _is_forbidden_domain_edge(source, target)
    ]
    assert domain_back_edges == []

    all_phase_importers = [
        source
        for source, targets in graph.items()
        if _imported_phases(targets) == set(PHASES)
    ]
    assert all_phase_importers == ["ai_org.driver"]


def _package_modules() -> dict[str, Path]:
    modules = {}
    for path in PACKAGE_ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).with_suffix("")
        parts = rel.parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        modules[".".join(parts)] = path
    return modules


def _module_imports(path: Path, module: str, modules: dict[str, Path]) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _add_if_local(imports, alias.name, modules)
        elif isinstance(node, ast.ImportFrom):
            for candidate in _from_import_candidates(node, module, path.name == "__init__.py"):
                _add_if_local(imports, candidate, modules)
    imports.discard(module)
    return imports


def _from_import_candidates(node: ast.ImportFrom, module: str, is_package: bool) -> list[str]:
    base = _absolute_from_base(node, module, is_package)
    candidates = []
    for alias in node.names:
        if alias.name == "*":
            candidates.append(base)
            continue
        candidates.append(f"{base}.{alias.name}" if base else alias.name)
        if base:
            candidates.append(base)
    return candidates


def _absolute_from_base(node: ast.ImportFrom, module: str, is_package: bool) -> str:
    if node.level == 0:
        return node.module or ""
    package_parts = module.split(".")
    if not is_package:
        package_parts = package_parts[:-1]
    parent_parts = package_parts[: len(package_parts) - node.level + 1]
    if node.module:
        parent_parts.extend(node.module.split("."))
    return ".".join(parent_parts)


def _add_if_local(imports: set[str], candidate: str, modules: dict[str, Path]) -> None:
    if not candidate.startswith("ai_org"):
        return
    parts = candidate.split(".")
    while parts:
        module = ".".join(parts)
        if module in modules:
            imports.add(module)
            return
        parts.pop()


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(module: str) -> list[str] | None:
        if module in visiting:
            start = stack.index(module)
            return stack[start:] + [module]
        if module in visited:
            return None
        visiting.add(module)
        stack.append(module)
        for target in sorted(graph.get(module, ())):
            cycle = visit(target)
            if cycle:
                return cycle
        stack.pop()
        visiting.remove(module)
        visited.add(module)
        return None

    for module in sorted(graph):
        cycle = visit(module)
        if cycle:
            return cycle
    return None


def _phase(module: str) -> str | None:
    for prefix in PHASES:
        if module == prefix or module.startswith(prefix + "."):
            return prefix
    return None


def _is_forbidden_domain_edge(source: str, target: str) -> bool:
    source_phase = _phase(source)
    target_phase = _phase(target)
    if source_phase is None or target_phase is None or source_phase == target_phase:
        return False
    return PHASES[target_phase] > PHASES[source_phase]


def _imported_phases(targets: set[str]) -> set[str]:
    return {phase for target in targets if (phase := _phase(target)) is not None}
