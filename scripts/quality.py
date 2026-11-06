"""One quality recipe for local development, pre-commit and CI."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from process import CommandIncompleteError, run_command
from test_suite import DEFAULT_CHILD_WATCHDOG_SECONDS, run_suite

PR_TEST_MODULES = (
    "tests/test_model_port.py",
    "tests/test_compatible_loopback.py",
    "tests/test_client_reads.py",
    "tests/test_reference_clients.py",
    "tests/test_release_artifacts.py",
)


async def check(
    base: str = "HEAD",
    *,
    pr: bool = False,
    required_roots: tuple[str, ...] = (),
    suite_deadline_seconds: float | None = DEFAULT_CHILD_WATCHDOG_SECONDS,
    command_deadline_seconds: float | None = DEFAULT_CHILD_WATCHDOG_SECONDS,
    python_evidence_path: Path | None = None,
) -> int:
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
        result = await run_command(
            command,
            capture=False,
            deadline_seconds=command_deadline_seconds,
            own_process_group=True,
        )
        if result.returncode:
            return result.returncode
    if pr:
        command = [python, "-m", "pytest", "--no-cov", "-q", *PR_TEST_MODULES]
        print("+ " + " ".join(command), flush=True)
        return (
            await run_command(
                command,
                capture=False,
                deadline_seconds=command_deadline_seconds,
                own_process_group=True,
            )
        ).returncode
    return await run_suite(
        python,
        required_roots,
        child_deadline_seconds=suite_deadline_seconds,
        evidence_path=python_evidence_path,
    )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", action="store_true")
    parser.add_argument("--suite-child-watchdog-seconds", type=float, default=1800.0)
    parser.add_argument("--command-watchdog-seconds", type=float, default=1800.0)
    parser.add_argument("--externally-supervised-suite", action="store_true")
    parser.add_argument("base", nargs="?", default="HEAD")
    args = parser.parse_args()
    watchdog = None if args.externally_supervised_suite else args.suite_child_watchdog_seconds
    try:
        return await check(
            args.base,
            pr=args.pr,
            suite_deadline_seconds=watchdog,
            command_deadline_seconds=args.command_watchdog_seconds,
        )
    except CommandIncompleteError as error:
        print(f"Quality check incomplete: {error.reason} in {error.phase}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
