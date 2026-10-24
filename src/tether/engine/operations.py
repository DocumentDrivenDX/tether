"""Shielded operation ownership spans raw executor return and adopted awaitable exit."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from contextlib import suppress
from contextvars import Context, copy_context
from threading import Event
from typing import Generic, TypeVar, cast

from tether.types.execution import ExecutionDenied

T = TypeVar("T")
MAX_AWAITABLE_CHAIN = 16


class Operation(Generic[T]):
    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.context = copy_context()
        self.completion: asyncio.Future[T] = self.loop.create_future()
        self.cancelled = Event()
        self.child: asyncio.Future[object] | None = None
        self.depth = 0
        self.anchor: asyncio.Future[object] | None = None
        self.power: int = 1
        self.distance: int = 0
        self.rejection: ExecutionDenied | None = None
        self.completion.add_done_callback(self._observe, context=Context())

    def cancel(self) -> None:
        self.cancelled.set()
        self._cancel_child()

    def _cancel_child(self) -> None:
        if self.child is not None and not self.child.done():
            # Failed cancellation cannot release actual ownership or mask the waiter.
            with suppress(BaseException):
                self.child.cancel()

    def _observe(self, future: asyncio.Future[T]) -> None:
        if not future.cancelled():
            future.exception()
        self.child = None
        self.anchor = None

    def failed(self, error: BaseException) -> None:
        if not self.completion.done():
            self.completion.set_exception(self.rejection or error)

    def raw_done(self, future: asyncio.Future[object]) -> None:
        try:
            self.adopt(future.result())
        except BaseException as error:
            self.failed(error)

    def _child_done(self, future: asyncio.Future[object]) -> None:
        try:
            self.adopt(future.result())
        except BaseException as error:
            self.failed(error)

    @staticmethod
    def _close_unstarted(value: object) -> None:
        if inspect.iscoroutine(value):
            value.close()

    def _future(self, value: Awaitable[object]) -> asyncio.Future[object]:
        if isinstance(value, asyncio.Future) and value.get_loop() is not self.loop:
            raise ExecutionDenied("worker_awaitable_loop")
        return self.context.copy().run(asyncio.ensure_future, value, loop=self.loop)

    def _cycle(self, child: asyncio.Future[object]) -> bool:
        if child is self.completion or child is self.anchor:
            return True
        self.distance += 1
        if self.anchor is None or self.distance == self.power:
            self.anchor = child
            self.power *= 2
            self.distance = 0
        return False

    def _fresh(self, value: Awaitable[object]) -> asyncio.Future[object] | None:
        if self.depth >= MAX_AWAITABLE_CHAIN and self.rejection is None:
            self.rejection = ExecutionDenied("worker_awaitable_depth")
        if self.cancelled.is_set() or self.rejection is not None:
            self._close_unstarted(value)
            self.failed(self.rejection or ExecutionDenied("worker_cancelled"))
            return None
        try:
            return self._future(value)
        except BaseException:
            self._close_unstarted(value)
            raise

    def _attach(self, child: asyncio.Future[object]) -> None:
        if child.get_loop() is not self.loop:
            raise ExecutionDenied("worker_awaitable_loop")
        if self._cycle(child):
            raise ExecutionDenied("worker_awaitable_cycle")
        self.depth = min(self.depth + 1, MAX_AWAITABLE_CHAIN + 1)
        if self.depth > MAX_AWAITABLE_CHAIN and self.rejection is None:
            self.rejection = ExecutionDenied("worker_awaitable_depth")
        self.child = child
        child.add_done_callback(self._child_done, context=Context())
        if self.cancelled.is_set() or self.rejection is not None:
            self._cancel_child()

    def adopt(self, value: object) -> None:
        if not inspect.isawaitable(value):
            if self.rejection is not None:
                self.failed(self.rejection)
            else:
                self.completion.set_result(cast(T, value))
            return
        child = value if isinstance(value, asyncio.Future) else self._fresh(value)
        if child is not None:
            self._attach(child)

    def start_async(
        self, function: Callable[..., T | Awaitable[T]], args: tuple[object, ...]
    ) -> None:
        try:
            self.adopt(function(*args))
        except BaseException as error:
            self.failed(error)

    async def wait(self) -> T:
        try:
            return await asyncio.shield(self.completion)
        except asyncio.CancelledError:
            self.cancel()
            raise
