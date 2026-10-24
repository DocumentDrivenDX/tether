"""Bounded offload whose capacity follows the actual worker, including cancellation."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from threading import Event
from typing import TypeVar, cast

from tether.types.execution import ExecutionDenied

T = TypeVar("T")


class Workers:
    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self.closed = False
        self.active: dict[asyncio.Task[object], str] = {}

    def busy(self, session_id: str) -> bool:
        return session_id in self.active.values()

    async def call(
        self, session_id: str, function: Callable[..., T | Awaitable[T]], *args: object
    ) -> T:
        if self.closed:
            raise ExecutionDenied("engine_closed")
        if inspect.iscoroutinefunction(function):
            return await cast(Awaitable[T], function(*args))
        if len(self.active) >= self.capacity:
            raise ExecutionDenied("worker_capacity")
        cancelled = Event()

        def invoke() -> T | Awaitable[T]:
            if cancelled.is_set():
                raise ExecutionDenied("worker_cancelled")
            return function(*args)

        task = asyncio.create_task(asyncio.to_thread(invoke))
        self.active[task] = session_id
        task.add_done_callback(self._finished)
        try:
            value = await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        if inspect.isawaitable(value):
            return cast(T, await value)
        return cast(T, value)

    def _finished(self, task: asyncio.Task[object]) -> None:
        self.active.pop(task, None)
        if not task.cancelled():
            task.exception()  # Observe a late failure without exposing content.

    async def close(self, seconds: float) -> bool:
        """Seal new submissions; report outstanding work after a finite wait."""
        self.closed = True
        if not self.active:
            return True
        _, pending = await asyncio.wait(tuple(self.active), timeout=seconds)
        return not pending
