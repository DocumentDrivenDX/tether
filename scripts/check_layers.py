"""Fail on imports outside architecture.md's named owners."""

from pathlib import Path

from boundaries.checker import check_tree


def main() -> int:
    problems = check_tree(Path.cwd())
    for problem in problems:
        print(problem)
    if not problems:
        print("Layer boundaries: pass")
    return bool(problems)


if __name__ == "__main__":
    raise SystemExit(main())
