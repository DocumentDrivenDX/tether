"""Finite caller waits retain physical acquisition/transaction/release tasks until exit."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from psycopg import Error

from tether.types.execution import CommitUnknown
from tether.types.storage import StorageLimits, StoreError

T = TypeVar("T")


class PendingIO:
    def __init__(self, limits: StorageLimits, on_idle: Callable[[], None] | None = None) -> None:
        self.limits, self.on_idle = limits.model_copy(deep=True), on_idle
        self.loop = asyncio.get_running_loop()
        self.active: set[asyncio.Task[object]] = set()
        self.closed = False

    def check_loop(self) -> None:
        if asyncio.get_running_loop() is not self.loop:
            raise RuntimeError("wrong_event_loop")

    async def run(self, operation: Callable[[], Awaitable[T]], *, write: bool = False) -> T:
        self.check_loop()
        if self.closed:
            raise StoreError("database_root_closed")
        if len(self.active) >= self.limits.max_pending_io:
            raise StoreError("database_capacity")
        task = asyncio.ensure_future(operation())
        self.active.add(task)
        task.add_done_callback(self._finished)
        try:
            return await asyncio.wait_for(asyncio.shield(task), self.limits.io_seconds)
        except (TimeoutError, Error) as error:
            if write:
                raise CommitUnknown("database_commit_unknown") from error
            raise StoreError("database_unavailable") from error

    def _finished(self, task: asyncio.Task[object]) -> None:
        self.active.discard(task)
        if not task.cancelled():
            task.exception()
        if not self.active and self.on_idle is not None:
            self.on_idle()

    async def aclose(self) -> bool:
        self.check_loop()
        self.closed = True
        if self.active:
            await asyncio.wait(self.active, timeout=self.limits.shutdown_seconds)
        if self.on_idle is not None:
            self.on_idle()
        return not self.active
