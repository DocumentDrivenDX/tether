"""Retained optional server ownership; caller cancellation never detaches physical work."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Self

from tether.types.execution import Actor
from tether.types.storage import StorageLimits, StoreError

from ..io import PendingIO
from ..root import DatabaseRoot
from ..source import Connection, ConnectionSource
from .config import EmbeddedConfig, EmbeddedEndpoint
from .files import Files, prepare
from .processes import Processes

_LIVE: set["EmbeddedPostgres"] = set()


class EmbeddedPostgres:
    def __init__(self, config: EmbeddedConfig) -> None:
        self.config = config.model_copy(deep=True)
        self._io = PendingIO(
            StorageLimits(
                io_seconds=config.startup_seconds,
                shutdown_seconds=config.shutdown_seconds,
                max_pending_io=config.max_roots,
            )
        )
        self._files: Files | None = None
        self._processes = Processes()
        self._roots: list[DatabaseRoot] = []
        self._connections = 0
        self._closing = False
        self._ready = False
        self._close_task: asyncio.Task[None] | None = None

    @classmethod
    async def start(cls, config: EmbeddedConfig) -> Self:
        server = cls(config)
        _LIVE.add(server)
        try:
            await server._io.run(server._boot)
        except BaseException:
            server._seal()
            server._ensure_close()
            raise
        return server

    async def _boot(self) -> None:
        self._files = await asyncio.to_thread(prepare, self.config)
        initialized = await asyncio.to_thread(self._files.initialized)
        if not initialized:
            await self._processes.initialize(self._files, self.config.bootstrap_user)
            await asyncio.to_thread(self._files.mark)
        await self._processes.start(
            self._files, self.config.poll_seconds, self.config.bootstrap_user
        )
        self._ready = True

    def _check(self) -> None:
        self._io.check_loop()
        if self._closing or not self._ready:
            raise StoreError("embedded_unavailable")

    @property
    def endpoint(self) -> EmbeddedEndpoint:
        self._check()
        if self._files is None:
            raise StoreError("embedded_unavailable")
        return EmbeddedEndpoint(host=str(self._files.runtime), user=self.config.bootstrap_user)

    def _source(self, source: ConnectionSource) -> ConnectionSource:
        @asynccontextmanager
        async def acquire(actor: Actor, session_id: str | None) -> AsyncGenerator[Connection, None]:
            self._check()
            self._connections += 1
            try:
                async with source(actor, session_id) as connection:
                    yield connection
            finally:
                self._connections -= 1

        return acquire

    async def open_root(
        self,
        source: ConnectionSource,
        *,
        actor: Actor,
        schema: str,
        limits: StorageLimits | None = None,
    ) -> DatabaseRoot:
        self._check()
        if len(self._roots) + len(self._io.active) >= self.config.max_roots:
            raise StoreError("embedded_root_capacity")

        async def open_root() -> DatabaseRoot:
            root = await DatabaseRoot.open(
                self._source(source), actor=actor, schema=schema, limits=limits
            )
            self._roots.append(root)
            if self._closing:
                root.workers.closed = True
                raise StoreError("embedded_closed")
            return root

        return await self._io.run(open_root)

    def _seal(self) -> None:
        self._io.check_loop()
        self._closing = True
        self._io.closed = True
        self._processes.closing = True
        for root in self._roots:
            root.workers.closed = True

    def _ensure_close(self) -> asyncio.Task[None]:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._finish())
            self._close_task.add_done_callback(self._observe_close)
        return self._close_task

    def _observe_close(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()

    async def _finish(self) -> None:
        while self._io.active:
            await asyncio.wait(tuple(self._io.active), timeout=self.config.shutdown_seconds)
        pending = True
        while pending:
            drained = [await root.aclose() for root in self._roots]
            pending = not all(drained) or self._connections != 0
            if pending:
                await asyncio.sleep(self.config.poll_seconds)
        await self._processes.stop()
        try:
            if self._files is not None:
                await asyncio.to_thread(self._files.release)
        finally:
            _LIVE.discard(self)

    async def aclose(self) -> bool:
        self._seal()
        task = self._ensure_close()
        try:
            await asyncio.wait_for(asyncio.shield(task), self.config.shutdown_seconds)
        except TimeoutError:
            return False
        return True
