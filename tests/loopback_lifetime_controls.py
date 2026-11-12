"""Owned local socket lifecycle controls for compatible adapter tests."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator

from selected_profiles_process import await_retained

from tether.adapters.openai_compat import OpenAICompatModel


async def _join_task(task: asyncio.Future) -> None:
    await asyncio.gather(task, return_exceptions=True)


async def _cancel_and_join(task: asyncio.Future) -> None:
    if not task.done():
        task.cancel()
    await await_retained(asyncio.create_task(_join_task(task)))


class OwnedLoopback:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.server: asyncio.Server | None = None
        self.handlers: set[asyncio.Task] = set()
        self.writers: set[asyncio.StreamWriter] = set()
        self.failure: asyncio.Future[BaseException] = asyncio.get_running_loop().create_future()
        self.failure_task: asyncio.Task | None = None
        self.observed_handlers: set[asyncio.Task] = set()
        self.model: OpenAICompatModel | None = None
        self.streams: list[AsyncGenerator] = []
        self.tasks: set[asyncio.Task] = set()
        self.observed_tasks: set[asyncio.Task] = set()
        self.cleanup_task: asyncio.Task | None = None

    def _connected(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writers.add(writer)
        coroutine = self.handler(reader, writer)
        try:
            task = asyncio.create_task(coroutine)
        except BaseException as error:
            coroutine.close()
            if not self.failure.done():
                self.failure.set_result(error)
            writer.close()
            return
        self.handlers.add(task)
        task.add_done_callback(self._handler_done)

    def _handler_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None and not self.failure.done():
            self.failure_task = task
            self.failure.set_result(error)

    async def __aenter__(self):
        start = asyncio.create_task(asyncio.start_server(self._connected, "127.0.0.1", 0))
        try:
            self.server = await await_retained(start)
        except BaseException as primary:
            if start.done() and not start.cancelled():
                with contextlib.suppress(BaseException):
                    self.server = start.result()
            cleanup = asyncio.create_task(self._cleanup())
            try:
                await await_retained(cleanup)
            except BaseException as cleanup_error:
                raise BaseExceptionGroup(
                    "loopback_enter_and_cleanup_failed", [primary, cleanup_error]
                ) from primary
            raise
        return self

    @property
    def port(self) -> int:
        assert self.server is not None
        assert self.server.sockets
        return int(self.server.sockets[0].getsockname()[1])

    def own_stream(self, stream: AsyncGenerator) -> AsyncGenerator:
        self.streams.append(stream)
        return stream

    def own_task(self, task: asyncio.Task) -> asyncio.Task:
        self.tasks.add(task)
        return task

    async def wait(self, operation, *terminals: asyncio.Task):
        task = asyncio.ensure_future(operation)
        try:
            done, _ = await asyncio.wait(
                (task, self.failure, *terminals), return_when=asyncio.FIRST_COMPLETED
            )
            if self.failure in done:
                if self.failure_task is not None:
                    self.observed_handlers.add(self.failure_task)
                await _cancel_and_join(task)
                raise self.failure.result()
            if task.done():
                for terminal in terminals:
                    if terminal in done:
                        self.observed_tasks.add(terminal)
                        await terminal
                return await task
            for terminal in terminals:
                if terminal in done:
                    await _cancel_and_join(task)
                    self.observed_tasks.add(terminal)
                    await terminal
                    raise AssertionError("loopback_terminal_before_expected_state")
            return await task
        except BaseException:
            await _cancel_and_join(task)
            raise

    async def wait_event(self, event: asyncio.Event, *terminals: asyncio.Task) -> None:
        waiter = asyncio.create_task(event.wait())
        try:
            done, _ = await asyncio.wait(
                (waiter, self.failure, *terminals), return_when=asyncio.FIRST_COMPLETED
            )
            if self.failure in done:
                if self.failure_task is not None:
                    self.observed_handlers.add(self.failure_task)
                raise self.failure.result()
            if waiter in done:
                await waiter
                return
            for terminal in terminals:
                if terminal in done:
                    await terminal
            raise AssertionError("loopback_terminal_before_expected_state")
        finally:
            if not waiter.done():
                waiter.cancel()
            await await_retained(asyncio.create_task(_join_task(waiter)))

    async def _cleanup(self) -> None:
        failures: list[BaseException] = []
        release = getattr(getattr(self.handler, "__self__", None), "release", None)
        if isinstance(release, asyncio.Event):
            release.set()
        await self._stop_listener(failures)
        await self._cancel_operations(failures)
        await self._close_streams_and_model(failures)
        await self._close_writers(failures)
        await self._join_handlers(failures)
        await self._await_listener_closed(failures)
        if failures:
            raise BaseExceptionGroup("loopback_cleanup_failed", failures)

    async def _stop_listener(self, failures: list[BaseException]) -> None:
        if self.server is not None:
            self.server.close()

    async def _await_listener_closed(self, failures: list[BaseException]) -> None:
        if self.server is not None:
            try:
                await self.server.wait_closed()
            except BaseException as error:
                failures.append(error)

    async def _cancel_operations(self, failures: list[BaseException]) -> None:
        for task in self.tasks:
            if not task.done():
                task.cancel()
        if self.tasks:
            tasks = tuple(self.tasks)
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            failures.extend(
                value
                for task, value in zip(tasks, outcomes, strict=True)
                if task not in self.observed_tasks
                and isinstance(value, BaseException)
                and not isinstance(value, asyncio.CancelledError)
            )

    async def _close_streams_and_model(self, failures: list[BaseException]) -> None:
        for stream in self.streams:
            try:
                await stream.aclose()
            except BaseException as error:
                failures.append(error)
        if self.model is not None:
            try:
                await self.model.aclose()
            except BaseException as error:
                failures.append(error)

    async def _close_writers(self, failures: list[BaseException]) -> None:
        for writer in self.writers:
            writer.close()
        if self.writers:
            outcomes = await asyncio.gather(
                *(writer.wait_closed() for writer in self.writers), return_exceptions=True
            )
            failures.extend(value for value in outcomes if isinstance(value, BaseException))

    async def _join_handlers(self, failures: list[BaseException]) -> None:
        if self.handlers:
            handlers = tuple(self.handlers)
            outcomes = await asyncio.gather(*handlers, return_exceptions=True)
            failures.extend(
                value
                for handler, value in zip(handlers, outcomes, strict=True)
                if handler not in self.observed_handlers
                and isinstance(value, BaseException)
                and not isinstance(value, asyncio.CancelledError)
            )

    async def __aexit__(self, exc_type, exc, tb) -> None:
        cleanup = asyncio.create_task(self._cleanup())
        self.cleanup_task = cleanup
        try:
            await await_retained(cleanup)
        except BaseException as error:
            if exc is not None:
                raise BaseExceptionGroup(
                    "loopback_test_and_cleanup_failed", [exc, error]
                ) from error
            raise


class HeldCleanupLoopback(OwnedLoopback):
    def __init__(self, handler) -> None:
        super().__init__(handler)
        self.cleanup_started = asyncio.Event()
        self.release_cleanup = asyncio.Event()

    async def _stop_listener(self, failures: list[BaseException]) -> None:
        self.cleanup_started.set()
        await self.release_cleanup.wait()
        await super()._stop_listener(failures)


class DeferredRequestTimeout:
    def __init__(self, seconds: float, timeout_factory) -> None:
        self.seconds = seconds
        self.context = timeout_factory(None)
        self.armed = False

    async def __aenter__(self):
        return await self.context.__aenter__()

    async def __aexit__(self, exc_type, exc, tb):
        return await self.context.__aexit__(exc_type, exc, tb)

    def arm(self) -> None:
        assert not self.armed
        self.context.reschedule(asyncio.get_running_loop().time() + self.seconds)
        self.armed = True
