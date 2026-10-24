"""Bounded tooling subprocesses: absolute executables, argv only, no shell."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: str
    stderr: str


async def run_command(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    capture: bool = True,
    deadline_seconds: float = 180,
) -> Result:
    if not args or not Path(args[0]).is_absolute():
        raise ValueError("tooling requires an absolute executable")
    stream = asyncio.subprocess.PIPE if capture else None
    child = await asyncio.create_subprocess_exec(*args, cwd=cwd, stdout=stream, stderr=stream)
    try:
        out, err = await asyncio.wait_for(child.communicate(), deadline_seconds)
    except BaseException:
        if child.returncode is None:
            child.kill()
            await child.wait()
        raise
    return Result(child.returncode or 0, (out or b"").decode(), (err or b"").decode())
