"""Fail closed on missing/raised ceilings or new shape-rule suppressions."""

from __future__ import annotations

import asyncio
import io
import re
import shutil
import sys
import tokenize
import tomllib
from pathlib import Path

from process import run_command

LIMITS = {
    "tool.ruff.lint.mccabe.max-complexity": 10,
    "tool.ruff.lint.pylint.max-returns": 6,
    "tool.ruff.lint.pylint.max-branches": 12,
    "tool.ruff.lint.pylint.max-statements": 40,
    "tool.ruff.lint.pylint.max-public-methods": 15,
    "tool.tether.ceilings.source_lines": 500,
    "tool.tether.ceilings.test_lines": 800,
}
SHAPE_RULES = ("C901", "PLR0904", "PLR0911", "PLR0912", "PLR0915")
NOQA = re.compile(r"no" + r"qa(?:\s*:\s*([A-Z0-9, ]+))?", re.IGNORECASE)


def disables_shape(code: str) -> bool:
    return code == "ALL" or any(rule.startswith(code) for rule in SHAPE_RULES)


def suppressed(source: str) -> bool:
    comments = (
        token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    )
    for comment in comments:
        match = NOQA.search(comment)
        if match and (
            not match[1] or any(disables_shape(c) for c in match[1].replace(",", " ").split())
        ):
            return True
    return False


def values(text: str) -> dict[str, int]:
    data = tomllib.loads(text)
    result = {}
    for key in LIMITS:
        node = data
        for part in key.split("."):
            node = node[part]
        if not isinstance(node, int) or isinstance(node, bool) or node < 1:
            raise ValueError(f"invalid ceiling: {key}")
        result[key] = node
    return result


def compare(before: str | None, after: str) -> list[str]:
    current = values(after)
    previous = values(before) if before is not None else LIMITS
    return [
        f"{key}: ceiling raised ({previous[key]} -> {value})"
        for key, value in current.items()
        if value > min(previous[key], LIMITS[key])
    ]


async def check(root: Path, base: str = "HEAD") -> list[str]:
    current = (root / "pyproject.toml").read_text(encoding="utf-8")
    git = shutil.which("git")
    if git is None:
        return ["git is required for ceiling baselines"]
    old = await run_command([git, "show", f"{base}:pyproject.toml"], cwd=root)
    ref = await run_command([git, "rev-parse", "--verify", "--end-of-options", base], cwd=root)
    if ref.returncode:
        return [f"cannot resolve ceiling baseline: {base}"]
    try:
        problems = compare(old.stdout if old.returncode == 0 else None, current)
    except (KeyError, TypeError, ValueError) as error:
        return [f"invalid ceiling configuration: {error}"]
    data = tomllib.loads(current)["tool"]["ruff"]["lint"]
    required = {"C901", "PLR", "PLR0904"}
    selected = set(data.get("select", [])) | set(data.get("extend-select", []))
    if not required.issubset(selected):
        problems.append("shape rules removed from linter selection")
    ignores = [
        *data.get("ignore", []),
        *data.get("extend-ignore", []),
        *(x for group in data.get("per-file-ignores", {}).values() for x in group),
    ]
    if any(disables_shape(x) for x in ignores):
        problems.append("shape rule disabled in linter configuration")
    for directory in ("src", "scripts", "tests"):
        for path in (root / directory).rglob("*.py"):
            if suppressed(path.read_text(encoding="utf-8")):
                problems.append(f"{path.relative_to(root)}: shape-rule suppression")
    return problems


async def main() -> int:
    problems = await check(Path.cwd(), sys.argv[1] if len(sys.argv) > 1 else "HEAD")
    for problem in problems:
        print(problem)
    return bool(problems)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
