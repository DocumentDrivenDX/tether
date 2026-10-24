"""Run the shared repository gates from Claude Code hooks."""

import asyncio
import json
import sys
from pathlib import Path

from process import run_command


async def main() -> int:
    payload = json.load(sys.stdin)
    mode = sys.argv[1]
    if mode == "stop" and payload.get("stop_hook_active"):
        return 0
    commands = [[sys.executable, "scripts/check_file_size.py"]]
    if mode == "stop":
        commands = [[sys.executable, "scripts/quality.py"]]
    else:
        filename = payload.get("tool_input", {}).get("file_path")
        if filename:
            path = await asyncio.to_thread(Path(filename).resolve)
            if path.is_relative_to(Path.cwd()) and path.suffix == ".py":
                commands.insert(0, [sys.executable, "-m", "ruff", "check", "--", str(path)])
    for command in commands:
        result = await run_command(command)
        if result.returncode:
            print((result.stdout + result.stderr)[-4000:], file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
