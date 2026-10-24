"""Extract imports everywhere, including function bodies and TYPE_CHECKING."""

from __future__ import annotations

import ast
from importlib.util import resolve_name


def qualified(node: ast.AST, aliases: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return qualified(node.value, aliases) + "." + node.attr
    return ""


def extract(
    tree: ast.AST, module: str, is_package: bool
) -> tuple[list[tuple[int, str]], dict[str, str]]:
    imports: list[tuple[int, str]] = []
    aliases: dict[str, str] = {}
    package = module if is_package else module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                imports.append((node.lineno, name.name))
                aliases[name.asname or name.name.split(".")[0]] = (
                    name.name if name.asname else name.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom):
            base = (
                resolve_name("." * node.level + (node.module or ""), package)
                if node.level
                else node.module or ""
            )
            for name in node.names:
                imports.append((node.lineno, base + "." + name.name))
                aliases[name.asname or name.name] = base + "." + name.name
    return imports, aliases


def dynamic_imports(tree: ast.AST, aliases: dict[str, str]) -> list[tuple[int, str]]:
    imports = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or qualified(node.func, aliases) not in (
            "__import__",
            "importlib.import_module",
        ):
            continue
        value = node.args[0] if node.args else None
        name = (
            value.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
            else "<computed-import>"
        )
        imports.append((node.lineno, name))
    return imports


def public_sdk_references(tree: ast.AST, aliases: dict[str, str]) -> list[tuple[int, str]]:
    annotations: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith(
            "_"
        ):
            annotations.extend(
                a.annotation
                for a in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
                if a.annotation
            )
            if node.returns:
                annotations.append(node.returns)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and not node.target.id.startswith("_")
        ):
            annotations.append(node.annotation)
    return [leak for annotation in annotations for leak in annotation_leaks(annotation, aliases)]


def annotation_leaks(node: ast.AST, aliases: dict[str, str]) -> list[tuple[int, str]]:
    from .policy import sdk_reference

    name = qualified(node, aliases)
    leaks = [(getattr(node, "lineno", 1), name)] if sdk_reference(name) else []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            parsed = ast.parse(node.value, mode="eval")
        except SyntaxError:
            return leaks
        return leaks + annotation_leaks(parsed, aliases)
    if isinstance(node, ast.Subscript):
        generic = qualified(node.value, aliases)
        if generic in {"typing.Literal", "typing_extensions.Literal"}:
            return leaks
        if generic in {"typing.Annotated", "typing_extensions.Annotated"}:
            target = node.slice.elts[0] if isinstance(node.slice, ast.Tuple) else node.slice
            return leaks + annotation_leaks(target, aliases)
    for child in ast.iter_child_nodes(node):
        leaks.extend(annotation_leaks(child, aliases))
    return leaks
