"""Bounded-memory stderr ownership and direct-child settlement for the installed API."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from typing import cast

from process import CommandIncompleteError

MAX_SERVER_STDERR_BYTES = 65_536


@dataclass(frozen=True)
class StderrDrain:
    bytes_observed: int
    overflowed: bool
    read_failed: bool


async def drain_stderr(reader: asyncio.StreamReader, overflow: asyncio.Event) -> StderrDrain:
    bytes_seen = 0
    overflowed = False
    try:
        while chunk := await reader.read(8192):
            bytes_seen += len(chunk)
            overflowed = overflowed or bytes_seen > MAX_SERVER_STDERR_BYTES
            if overflowed:
                overflow.set()
    except asyncio.CancelledError:
        raise
    except Exception:
        return StderrDrain(bytes_seen, overflowed, True)
    return StderrDrain(bytes_seen, overflowed, False)


def _drain_failed(task: asyncio.Task[StderrDrain]) -> bool:
    if task.cancelled():
        return True
    try:
        return task.exception() is not None or task.result().read_failed
    except BaseException:
        return True


async def _settle_wait(task: asyncio.Task[int]) -> bool:
    try:
        await task
    except BaseException:
        return True
    return False


def _close_owned_stderr(server: asyncio.subprocess.Process) -> None:
    transport = cast(asyncio.SubprocessTransport | None, getattr(server, "_transport", None))
    if transport is None:
        return
    pipe = transport.get_pipe_transport(2)
    if pipe is not None:
        pipe.close()


async def _replace_failed_capture(
    server: asyncio.subprocess.Process,
    drain_task: asyncio.Task[StderrDrain],
    overflow: asyncio.Event,
    fallback_started: bool,
) -> tuple[asyncio.Task[StderrDrain], bool]:
    if not fallback_started and server.stderr is not None:
        return asyncio.create_task(drain_stderr(server.stderr, overflow)), True
    _close_owned_stderr(server)
    if server.returncode is None:
        with suppress(ProcessLookupError):
            server.kill()
    return drain_task, fallback_started


async def stop_server(
    server: asyncio.subprocess.Process,
    stderr_task: asyncio.Task[StderrDrain],
    overflow: asyncio.Event,
) -> None:
    if server.returncode is None:
        with suppress(ProcessLookupError):
            server.terminate()
    waiter = asyncio.create_task(server.wait())
    drain_task = stderr_task
    fallback_started = False
    capture_failed = False
    wait_failed = False
    while True:
        if not drain_task.done():
            watched = {drain_task} if waiter.done() else {waiter, drain_task}
            await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
        if waiter.done():
            wait_failed = await _settle_wait(waiter)
        if drain_task.done():
            if _drain_failed(drain_task):
                capture_failed = True
                drain_task, fallback_started = await _replace_failed_capture(
                    server, drain_task, overflow, fallback_started
                )
                if not fallback_started or drain_task.done():
                    wait_failed = await _settle_wait(waiter) or wait_failed
                    break
                continue
            capture_failed = capture_failed or drain_task.result().overflowed
            wait_failed = await _settle_wait(waiter) or wait_failed
            break
    if wait_failed:
        raise CommandIncompleteError("cleanup_unconfirmed", "cleanup") from None
    if capture_failed:
        raise CommandIncompleteError("output_capture_failed", "execution")
