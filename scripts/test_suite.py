"""Run every collected case in bounded children, with one aggregate coverage gate."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from process import run_command

NATIVE_CASE_PREFIXES = ("tests/test_embedded_", "tests/test_postgres_")


async def command(arguments: list[str]) -> int:
    print("+ " + " ".join(arguments), flush=True)
    return (await run_command(arguments, capture=False)).returncode


def groups(values: object) -> list[list[str]]:
    if not isinstance(values, list) or not values:
        raise ValueError("empty_test_inventory")
    result: list[list[str]] = []
    current: list[str] = []
    seen: set[str] = set()
    used = 0
    native = False
    for value in values:
        if not isinstance(value, str) or not value or value in seen:
            raise ValueError("invalid_test_inventory")
        size = len(value.encode("utf-8")) + 1
        if size > 64 * 1024:
            raise ValueError("test_id_too_large")
        is_native = value.startswith(NATIVE_CASE_PREFIXES)
        limit = 8 if is_native else 32
        if current and (native != is_native or len(current) >= limit or used + size > 64 * 1024):
            result.append(current)
            current, used = [], 0
        current.append(value)
        used += size
        native = is_native
        seen.add(value)
    return [*result, current]


async def run_suite(python: str) -> int:
    code = await command([python, "-m", "coverage", "erase"])
    if code:
        return code
    with tempfile.TemporaryDirectory(prefix="tether-test-inventory-") as directory:
        inventory = Path(directory) / "cases.json"
        collector = Path(__file__).with_name("collect_tests.py").resolve()
        code = await command(
            [python, "-m", "coverage", "run", "--append", str(collector), str(inventory)]
        )
        if code:
            return code
        text = await asyncio.to_thread(inventory.read_text, encoding="utf-8")
        batches = groups(json.loads(text))
        print(
            f"Collected {sum(map(len, batches))} cases in {len(batches)} bounded groups", flush=True
        )
        for batch in batches:
            code = await command(
                [python, "-m", "coverage", "run", "--append", "-m", "pytest", "--no-cov", *batch]
            )
            if code:
                return code
    # Reads the unchanged branch/source/fail_under settings from pyproject.toml.
    return await command([python, "-m", "coverage", "report"])


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_suite(sys.executable)))
