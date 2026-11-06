"""Apply global source/test line caps to handwritten Python, including spike evidence."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Iterator
from pathlib import Path

SKIP_DIRS = frozenset({".git", ".venv", "dist", "build", "node_modules", "__pycache__"})


def python_files(root: Path) -> Iterator[Path]:
    """Prune existing exclusions before traversing installed dependencies."""
    for directory, children, names in os.walk(root):
        children[:] = sorted(name for name in children if name not in SKIP_DIRS)
        for name in sorted(names):
            if name.endswith(".py"):
                yield Path(directory) / name


def check_sizes(root: Path) -> list[str]:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    limits = data["tool"]["tether"]["ceilings"]
    problems = []
    for file in python_files(root):
        relative = file.relative_to(root)
        is_test = "tests" in relative.parts or file.name.startswith("test_")
        limit = limits["test_lines" if is_test else "source_lines"]
        lines = len(file.read_text(encoding="utf-8").splitlines())
        if lines > limit:
            problems.append(f"{relative}: {lines} lines exceed {limit}; split the file")
    return problems


def main() -> int:
    problems = check_sizes(Path.cwd())
    for problem in problems:
        print(problem)
    return bool(problems)


if __name__ == "__main__":
    raise SystemExit(main())
