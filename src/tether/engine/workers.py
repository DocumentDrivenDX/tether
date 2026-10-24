"""Separate physical executor permits from actual retained operation completion."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from contextvars import Context
from typing import TypeVar, cast

from tether.types.execution import ExecutionDenied
from tether.types.lifetime import WorkerLifetime

from .operations import Operation

T = TypeVar("T")


class Workers:
    def __init__(self, capacity: int, *, operation_capacity: int = 32) -> None:
        self.capacity, self.operation_capacity = capacity, operation_capacity
        self.closed = False
        self.scopes: dict[str, WorkerLifetime] = {}
        self.local_scope = WorkerLifetime()
        self.active: dict[asyncio.Future[object], str] = {}
        self.executors: set[asyncio.Future[object]] = set()

    def busy(self, session_id: str) -> bool:
        scope = self.scopes.get(session_id)
        return session_id in self.active.values() or (scope is not None and scope.busy(session_id))

    def _operation(
        self, session_id: str, function: Callable[..., T | Awaitable[T]]
    ) -> Operation[T]:
        scope = self.scopes.get(session_id, self.local_scope)
        scope.check_loop()
        if self.closed or scope.closed:
            raise ExecutionDenied("engine_closed")
        if not inspect.iscoroutinefunction(function) and len(self.executors) >= self.capacity:
            raise ExecutionDenied("worker_capacity")
        if len(self.active) >= self.operation_capacity:
            raise ExecutionDenied("operation_capacity")
        operation: Operation[T] = Operation()
        completion = cast(asyncio.Future[object], operation.completion)
        scope.retain(session_id, completion)
        self.active[completion] = session_id
        completion.add_done_callback(self._finished, context=Context())
        return operation

    async def call(
        self, session_id: str, function: Callable[..., T | Awaitable[T]], *args: object
    ) -> T:
        synchronous = not inspect.iscoroutinefunction(function)
        operation: Operation[T] = self._operation(session_id, function)
        if not synchronous:
            operation.start_async(function, args)
        else:
            self._submit(operation, function, args)
        return await operation.wait()

    def _submit(
        self,
        operation: Operation[T],
        function: Callable[..., T | Awaitable[T]],
        args: tuple[object, ...],
    ) -> None:
        def invoke() -> T | Awaitable[T]:
            if operation.cancelled.is_set():
                raise ExecutionDenied("worker_cancelled")
            return function(*args)

        try:
            task = cast(
                asyncio.Future[object],
                asyncio.get_running_loop().run_in_executor(
                    None, operation.context.copy().run, invoke
                ),
            )
        except BaseException as error:
            operation.failed(error)
            return
        self.executors.add(task)
        task.add_done_callback(self._executor_finished, context=Context())
        task.add_done_callback(operation.raw_done, context=Context())

    def _executor_finished(self, task: asyncio.Future[object]) -> None:
        self.executors.discard(task)

    def _finished(self, task: asyncio.Future[object]) -> None:
        self.active.pop(task, None)
        if not task.cancelled():
            task.exception()

    async def close(self, seconds: float) -> bool:
        self.closed = True
        if not self.active:
            return True
        _, pending = await asyncio.wait(tuple(self.active), timeout=seconds)
        return not pending
