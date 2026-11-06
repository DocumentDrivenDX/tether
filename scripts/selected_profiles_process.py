"""Clean-process launcher for live selected-profile qualification."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")


def _runtime_path(executables: tuple[str | None, ...]) -> str:
    directories = [
        str(Path(executable).absolute().parent)
        for executable in executables
        if executable is not None
    ]
    directories.extend(("/usr/bin", "/bin", "/usr/sbin", "/sbin"))
    return ":".join(dict.fromkeys(directories))


def exec_with_profile_environment(script: Path, argv: list[str], source_path: Path) -> None:
    """Restart once with a minimal environment before SDK profile resolution."""
    marker = "--internal-profile-environment-clean"
    if marker in argv:
        argv.remove(marker)
        return
    clean_environment = {
        "HOME": str(Path.home()),
        "PATH": _runtime_path(tuple(shutil.which(name) for name in ("node", "npm", "databricks"))),
        "PYTHONPATH": str(source_path),
    }
    result = subprocess.run(  # noqa: S603 - the resolved interpreter reexecs this fixed script
        [sys.executable, str(script.resolve()), marker, *argv[1:]],
        env=clean_environment,
        check=False,
    )
    raise SystemExit(result.returncode)


async def await_retained(task: asyncio.Task[T]) -> T:
    """Wait through repeated caller cancellation without canceling owned cleanup."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


def wait_for_process_group_exit(pgid: int) -> None:
    """Wait for absence of a group created and owned by the caller."""
    while True:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            raise RuntimeError("managed_peer_group_absence_unconfirmed") from None
        time.sleep(0.01)


__all__ = [
    "_runtime_path",
    "await_retained",
    "exec_with_profile_environment",
    "wait_for_process_group_exit",
]
