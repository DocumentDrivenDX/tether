"""Supervise one Agent task and finish in-flight fenced heartbeats before lease release."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

from tether.types.storage import SessionTurnLog

T = TypeVar("T")


async def heartbeat(log: SessionTurnLog, interval: float, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            async with asyncio.timeout(interval):
                await stop.wait()
        except TimeoutError:
            pass
        if not stop.is_set():
            await log.heartbeat()


async def finish(runner: asyncio.Future[T], lease: asyncio.Task[None], stop: asyncio.Event) -> None:
    runner.cancel()
    stop.set()
    await asyncio.gather(runner, lease, return_exceptions=True)


async def supervise(work: Awaitable[T], log: SessionTurnLog, interval: float) -> T:
    runner = asyncio.ensure_future(work)
    stop = asyncio.Event()
    lease = asyncio.create_task(heartbeat(log, interval, stop))
    try:
        done, _ = await asyncio.wait((runner, lease), return_when=asyncio.FIRST_COMPLETED)
        if lease in done:
            await lease
            raise RuntimeError("heartbeat_stopped")
        return await runner
    finally:
        cleanup = asyncio.create_task(finish(runner, lease, stop))
        cancelled = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cancelled = True
        await cleanup
        if cancelled:
            raise asyncio.CancelledError
