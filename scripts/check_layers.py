"""Fail on imports outside architecture.md's named owners."""

from pathlib import Path

from boundaries.checker import check_tree
from boundaries.debt import load_debt


def main() -> int:
    problems = check_tree(Path.cwd())
    for problem in problems:
        print(problem)
    if not problems:
        print("Layer boundaries: pass")
        count = sum(map(len, load_debt(Path.cwd()).values()))
        if count:
            print(f"Recorded WP-10 migration exceptions: {count}; release gate requires zero")
    return bool(problems)


if __name__ == "__main__":
    raise SystemExit(main())
