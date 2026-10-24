"""Check imports and graph cycles without importing production modules."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

from .imports import dynamic_imports, extract, public_sdk_references
from .policy import Violation, allowed, owner, within


def cycles(graph: dict[str, set[str]]) -> list[str]:
    done: set[str] = set()
    active: list[str] = []
    found: list[str] = []

    def visit(node: str) -> None:
        if node in active:
            found.append(" -> ".join([*active[active.index(node) :], node]))
            return
        if node in done:
            return
        active.append(node)
        for child in sorted(graph[node]):
            visit(child)
        active.pop()
        done.add(node)

    for node in sorted(graph):
        visit(node)
    return found


def module_name(path: Path, root: Path) -> str:
    parts = path.relative_to(root).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def inspect_module(
    path: Path, root: Path, extras: frozenset[str]
) -> tuple[list[Violation], list[str]]:
    module = module_name(path, root)
    problems: list[Violation] = []
    if owner(module) is None:
        problems.append(Violation(str(path), 1, "module has no architecture owner"))
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports, aliases = extract(tree, module, path.stem == "__init__")
        imports.extend(dynamic_imports(tree, aliases))
    except (SyntaxError, ImportError) as error:
        return [Violation(str(path), 1, f"cannot analyze source: {error}")], []
    for line, target in imports:
        if target.endswith(".*") or not allowed(module, target, extras):
            problems.append(Violation(str(path), line, f"{module} cannot import {target}"))
    if not any(
        within(module, p) for p in ("tether.runtime.pydantic_ai", "tether.adapters.pydantic_ai")
    ):
        for line, target in public_sdk_references(tree, aliases):
            problems.append(
                Violation(str(path), line, f"public annotation exposes SDK owner: {target}")
            )
    return problems, [target for _, target in imports]


def check_tree(project: Path) -> list[Violation]:
    root = project / "src"
    files = sorted((root / "tether").rglob("*.py"))
    if not files:
        return [Violation(str(root), 1, "no Tether Python source found")]
    data = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    extras = frozenset(data.get("project", {}).get("optional-dependencies", {}))
    modules = {module_name(p, root): p for p in files}
    graph: dict[str, set[str]] = {module: set() for module in modules}
    problems: list[Violation] = []
    for module, path in modules.items():
        errors, targets = inspect_module(path, root, extras)
        problems.extend(errors)
        for target in targets:
            candidates = [m for m in modules if within(target, m)]
            if candidates:
                graph[module].add(max(candidates, key=len))
    problems.extend(Violation(str(root), 1, "import cycle: " + c) for c in cycles(graph))
    return problems
