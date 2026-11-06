"""Check imports and graph cycles without importing production modules."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

from .debt import load_debt
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
    path: Path, root: Path, extras: frozenset[str], debt: frozenset[str] = frozenset()
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
        if target not in debt and (target.endswith(".*") or not allowed(module, target, extras)):
            problems.append(Violation(str(path), line, f"{module} cannot import {target}"))
    if not any(
        within(module, p) for p in ("tether.runtime.pydantic_ai", "tether.adapters.pydantic_ai")
    ):
        for line, target in public_sdk_references(tree, aliases):
            problems.append(
                Violation(str(path), line, f"public annotation exposes SDK owner: {target}")
            )
    return problems, [target for _, target in imports]


def source_modules(project: Path) -> tuple[dict[str, tuple[Path, Path]], list[Violation]]:
    roots = [project / "src", *sorted(project.glob("packages/*/src"))]
    modules: dict[str, tuple[Path, Path]] = {}
    problems = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            module = module_name(path, root)
            if module in modules:
                problems.append(Violation(str(path), 1, f"duplicate namespace owner: {module}"))
            modules[module] = (path, root)
    return modules, problems


def private_consumer_target(module: str, target: str, modules: set[str]) -> bool:
    consumers = {"tether.cli", "tether.tui", "tether_web_api", "tether_databricks"}
    if not any(within(module, consumer) for consumer in consumers):
        return False
    roots = {
        "tether.client",
        "tether.client.remote",
        "tether.client.postgres",
        "tether.client.compatible",
        "tether.client.databricks",
        "tether.client.testing",
        "tether_web_api",
        "tether_web_ui",
    }
    public = max((root for root in roots if within(target, root)), key=len, default=None)
    if public is None or within(module, public):
        return False
    dependency = max((name for name in modules if within(target, name)), key=len, default=public)
    return dependency != public or any(part.startswith("_") for part in target.split("."))


def check_tree(project: Path) -> list[Violation]:
    root = project / "src"
    modules, problems = source_modules(project)
    if not any(within(module, "tether") for module in modules):
        return [Violation(str(root), 1, "no Tether Python source found")]
    data = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    extras = frozenset(data.get("project", {}).get("optional-dependencies", {}))
    try:
        debt = load_debt(project)
    except (OSError, ValueError, TypeError, AttributeError) as error:
        return [Violation(str(root), 1, f"invalid interface debt inventory: {error}")]
    graph: dict[str, set[str]] = {module: set() for module in modules}
    for module, (path, source_root) in modules.items():
        permitted = debt.get(path.relative_to(project).as_posix(), frozenset())
        errors, targets = inspect_module(path, source_root, extras, permitted)
        problems.extend(errors)
        for target in targets:
            if target not in permitted and private_consumer_target(module, target, set(modules)):
                problems.append(
                    Violation(str(path), 1, f"consumer imports private module: {target}")
                )
            candidates = [m for m in modules if within(target, m)]
            if candidates:
                graph[module].add(max(candidates, key=len))
    problems.extend(Violation(str(root), 1, "import cycle: " + c) for c in cycles(graph))
    return problems
