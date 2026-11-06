"""Retained ownership for the live qualifier's small metadata/control children."""

from __future__ import annotations

import asyncio
import contextlib
from asyncio.subprocess import Process

from qualify_live_apps_incomplete import LiveQualificationIncomplete
from selected_profiles_process import await_retained


async def capture_owned_command(
    executable: str,
    *arguments: str,
    environment: dict[str, str],
    timeout_seconds: float | None,
    incomplete_reason: str,
) -> tuple[int | None, bytes | None]:
    """Capture a child while retaining spawn, drain and kill/reap through cancellation."""
    process = await spawn_owned_process(executable, *arguments, environment=environment)
    communication = asyncio.create_task(process.communicate())
    try:
        done, _ = await asyncio.wait({communication}, timeout=timeout_seconds)
    except asyncio.CancelledError:
        await await_retained(asyncio.create_task(_kill_and_drain(process, communication)))
        raise
    if communication not in done:
        await await_retained(asyncio.create_task(_kill_and_drain(process, communication)))
        raise LiveQualificationIncomplete(incomplete_reason)
    try:
        stdout, _stderr = communication.result()
    except asyncio.CancelledError:
        await await_retained(asyncio.create_task(_kill_and_reap(process)))
        raise
    except Exception:
        await await_retained(asyncio.create_task(_kill_and_reap(process)))
        raise LiveQualificationIncomplete(incomplete_reason) from None
    return process.returncode, stdout


async def spawn_owned_process(
    executable: str,
    *arguments: str,
    environment: dict[str, str],
) -> Process:
    """Settle a cancellation-raced spawn and reap the exact created process."""
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(
            executable,
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=environment,
        )
    )
    cancelled = False
    while not spawn.done():
        try:
            await asyncio.shield(spawn)
        except asyncio.CancelledError:
            cancelled = True
    process = spawn.result()
    if cancelled:
        await await_retained(asyncio.create_task(_kill_and_reap(process)))
        raise asyncio.CancelledError
    return process


async def _kill_and_drain(
    process: Process, communication: asyncio.Task[tuple[bytes | None, bytes | None]]
) -> None:
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    process_wait = asyncio.create_task(process.wait())
    if not communication.done():
        await asyncio.wait({process_wait, communication}, return_when=asyncio.FIRST_COMPLETED)
    if communication.done():
        try:
            communication.result()
        except BaseException:
            # A terminal communication failure leaves no reader draining the pipe.
            # Start one replacement only after the original task has ended.
            await await_retained(asyncio.create_task(_kill_and_reap(process)))
            await await_retained(process_wait)
            return
    await await_retained(process_wait)
    await communication


async def _kill_and_reap(process: Process) -> None:
    drain = asyncio.create_task(process.communicate())
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    try:
        await await_retained(drain)
    finally:
        await await_retained(asyncio.create_task(process.wait()))


__all__ = ["capture_owned_command", "spawn_owned_process"]
