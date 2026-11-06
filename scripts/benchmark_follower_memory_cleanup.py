"""Retained cleanup helpers for the fabricated follower-memory benchmark."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from typing import Any, TypeVar

from selected_profiles_process import await_retained

Close = Callable[[], Awaitable[bool]]
T = TypeVar("T")


async def run_retained(awaitable: Coroutine[Any, Any, T]) -> T:
    task = asyncio.create_task(awaitable)
    return await await_retained(task)


async def _close_all(resources: Sequence[tuple[str, Close]]) -> None:
    failures: list[BaseException] = []
    for name, close in reversed(resources):
        try:
            closed = await close()
            if closed is not True:
                raise RuntimeError(f"benchmark_follower_{name}_close_incomplete")
        except BaseException as error:
            failures.append(error)
    if failures:
        raise failures[0]


async def close_all_retained(resources: Sequence[tuple[str, Close]]) -> None:
    """Try every owned close, then surface failure despite repeated cancellation."""
    task = asyncio.create_task(_close_all(resources))
    await await_retained(task)


async def cancel_and_join(tasks: Sequence[asyncio.Task[Any]]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    failures = [
        result
        for result in results
        if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError)
    ]
    if failures:
        raise failures[0]
