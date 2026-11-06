"""Focused web suites preserve exact collected cases and existing child bounds."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from process import run_command
from test_suite import groups


def resolve_paths(root: Path, patterns: tuple[str, ...]) -> tuple[str, ...]:
    selected = []
    for pattern in patterns:
        matches = [
            path.relative_to(root).as_posix()
            for path in sorted(root.glob(pattern))
            if (path.is_file() and path.name.startswith("test_"))
            or (path.is_dir() and any(path.rglob("test_*.py")))
        ]
        if not matches:
            raise ValueError(f"unbuilt_required_test_suite: {pattern}")
        selected.extend(matches)
    return tuple(dict.fromkeys(selected))


async def run_selected(
    patterns: tuple[str, ...],
    *,
    deadline_seconds: float = 30 * 60,
    own_process_group: bool = True,
) -> int:
    root = Path.cwd().resolve()
    paths = await asyncio.to_thread(resolve_paths, root, patterns)
    python = sys.executable
    with tempfile.TemporaryDirectory(prefix="tether-web-inventory-") as directory:
        inventory = Path(directory) / "cases.json"
        collector = Path(__file__).with_name("collect_tests.py").resolve()
        result = await run_command(
            [python, str(collector), str(inventory), "--rootdir", str(root), *paths],
            cwd=root,
            capture=False,
            deadline_seconds=deadline_seconds,
            own_process_group=own_process_group,
        )
        if result.returncode:
            return result.returncode
        source = await asyncio.to_thread(inventory.read_text, encoding="utf-8")
        batches = groups(json.loads(source))
        print(f"Selected {sum(map(len, batches))} cases in {len(batches)} bounded groups")
        for batch in batches:
            with tempfile.TemporaryDirectory(prefix="tether-web-test-") as test_directory:
                result = await run_command(
                    [
                        python,
                        "-m",
                        "pytest",
                        "--no-cov",
                        "--basetemp",
                        test_directory,
                        "--rootdir",
                        str(root),
                        *batch,
                    ],
                    cwd=root,
                    capture=False,
                    deadline_seconds=deadline_seconds,
                    own_process_group=own_process_group,
                )
            if result.returncode:
                return result.returncode
    return 0
