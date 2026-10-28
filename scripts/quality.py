"""One quality recipe for local development, pre-commit and CI."""

from __future__ import annotations

import argparse
import asyncio
import sys

from process import run_command
from test_suite import run_suite

PR_TEST_MODULES = (
    "tests/test_model_port.py",
    "tests/test_compatible_loopback.py",
    "tests/test_client_reads.py",
    "tests/test_reference_clients.py",
    "tests/test_release_artifacts.py",
)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", action="store_true")
    parser.add_argument("base", nargs="?", default="HEAD")
    args = parser.parse_args()
    base = args.base
    python = sys.executable
    commands = [
        [python, "-m", "ruff", "check", "."],
        [python, "-m", "ruff", "format", "--check", "."],
        [python, "-m", "pyright"],
        [python, "scripts/check_file_size.py"],
        [python, "scripts/check_ceilings.py", base],
        [python, "scripts/check_layers.py"],
        [python, "scripts/check_dependencies.py"],
    ]
    for command in commands:
        print("+ " + " ".join(command), flush=True)
        result = await run_command(command, capture=False)
        if result.returncode:
            return result.returncode
    if args.pr:
        command = [python, "-m", "pytest", "--no-cov", "-q", *PR_TEST_MODULES]
        print("+ " + " ".join(command), flush=True)
        return (await run_command(command, capture=False)).returncode
    return await run_suite(python)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
