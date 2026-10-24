"""Separate physical executor permits from actual retained operation completion."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from contextvars import Context
from typing import TypeVar, cast

from tether.types.execution import ExecutionDenied
from tether.types.lifetime import WorkerLifetime

from .observers.worker import Outcome, WorkerObservation
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
        self,
        session_id: str,
        function: Callable[..., T | Awaitable[T]],
        *args: object,
        observe: WorkerObservation | None = None,
    ) -> T:
        with observe.waiting() if observe is not None else nullcontext():
            try:
                operation: Operation[T] = self._operation(session_id, function)
            except BaseException:
                if observe is not None:
                    observe.emit("refused")
                raise
            if observe is not None:
                operation.completion.add_done_callback(
                    lambda future: observed_completion(observe, future), context=Context()
                )
            if inspect.iscoroutinefunction(function):
                if observe is None:
                    operation.start_async(function, args)
                else:
                    operation.start_async(observed_async(function, observe), args)
            else:
                self._submit(operation, function, args, observe)
            try:
                return await operation.wait()
            except asyncio.CancelledError:
                if observe is not None:
                    observe.abandoned(not operation.completion.done())
                raise

    def _submit(
        self,
        operation: Operation[T],
        function: Callable[..., T | Awaitable[T]],
        args: tuple[object, ...],
        observe: WorkerObservation | None,
    ) -> None:
        def invoke() -> T | Awaitable[T]:
            if operation.cancelled.is_set():
                raise ExecutionDenied("worker_cancelled")
            with observe.body(True) if observe is not None else nullcontext():
                value = function(*args)
                if observe is not None:
                    observe.returned(inspect.isawaitable(value))
                return value

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


def observed_async(
    function: Callable[..., T | Awaitable[T]],
    observe: WorkerObservation,
) -> Callable[..., Awaitable[T]]:
    async def invoke(*args: object) -> T:
        with observe.body(False):
            value = function(*args)
            # The selected function is async; await its initial body only. Operation
            # owns any further awaitable returned by that body independently.
            result = await cast(Awaitable[T], value)
            observe.returned(inspect.isawaitable(result))
            return result

    return invoke


def observed_completion(observe: WorkerObservation, future: asyncio.Future[T]) -> None:
    outcome: Outcome = "returned"
    if future.cancelled():
        outcome = "cancelled"
    else:
        error = future.exception()
        if error is not None:
            outcome = "cancelled" if isinstance(error, asyncio.CancelledError) else "raised"
    observe.emit("completed", outcome)
