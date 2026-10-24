"""One quality recipe for local development, pre-commit and CI."""

from __future__ import annotations

import asyncio
import sys

from process import run_command


async def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "HEAD"
    python = sys.executable
    commands = [
        [python, "-m", "ruff", "check", "."],
        [python, "-m", "ruff", "format", "--check", "."],
        [python, "-m", "pyright"],
        [python, "scripts/check_file_size.py"],
        [python, "scripts/check_ceilings.py", base],
        [python, "scripts/check_layers.py"],
        [python, "scripts/check_dependencies.py"],
        [python, "-m", "pytest"],
    ]
    for command in commands:
        print("+ " + " ".join(command), flush=True)
        result = await run_command(command, capture=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
