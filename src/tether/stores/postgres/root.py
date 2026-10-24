"""Bounded borrowed database transactions and explicit root admission/lifetime."""

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import copy_context
from typing import Self, TypeVar, cast

from psycopg import AsyncConnection

from tether.types.execution import Actor, CommitUnknown, copy_actor
from tether.types.lifetime import WorkerLifetime
from tether.types.storage import IntegrityError, StorageLimits

from .io import PendingIO
from .namespace import Namespace, acquire
from .schema import idle, inspect, qualified
from .source import ConnectionSource as ConnectionSource

T = TypeVar("T")


class DatabaseRoot:
    def __init__(self, source: ConnectionSource, schema: str, limits: StorageLimits) -> None:
        qualified(schema)
        self.source, self.schema, self.limits = source, schema, limits.model_copy(deep=True)
        self._namespace: Namespace | None = None
        self._io = PendingIO(self.limits, self._release)

    @classmethod
    async def open(
        cls,
        source: ConnectionSource,
        *,
        actor: Actor,
        schema: str,
        limits: StorageLimits | None = None,
    ) -> Self:
        root = cls(source, schema, limits or StorageLimits())

        async def bootstrap() -> None:
            async with source(copy_actor(actor), None) as connection:
                idle(connection)
                async with connection.transaction():
                    identity = await inspect(connection, schema)
            root._namespace = acquire(identity, root)

        try:
            await root._io.run(bootstrap)
        except BaseException:
            root._io.closed = True
            root._release()
            raise
        return root

    def check_loop(self) -> None:
        self._io.check_loop()

    @property
    def workers(self) -> WorkerLifetime:
        if self._namespace is None:
            raise RuntimeError("database_root_not_open")
        return self._namespace.workers

    async def _transaction(
        self,
        actor: Actor,
        session_id: str | None,
        operation: Callable[[AsyncConnection[tuple[object, ...]]], Awaitable[T]],
        write: bool,
    ) -> T:
        complete = False
        try:
            async with self.source(copy_actor(actor), session_id) as connection:
                idle(connection)
                async with connection.transaction():
                    identity = await inspect(connection, self.schema)
                    if self._namespace is None or identity != self._namespace.identity:
                        raise IntegrityError("database_namespace_mismatch")
                    result = await operation(connection)
                    complete = True
            return result
        except Exception as error:
            if complete and write:
                raise CommitUnknown("database_release_unknown") from error
            raise

    async def run(
        self,
        actor: Actor,
        session_id: str | None,
        operation: Callable[[AsyncConnection[tuple[object, ...]]], Awaitable[T]],
        *,
        write: bool = False,
    ) -> T:
        actor = copy_actor(actor)
        return await self._io.run(
            lambda: self._transaction(actor, session_id, operation, write), write=write
        )

    async def prepare(self, session_id: str, operation: Callable[[], T]) -> T:
        """Retain actual pure reconstruction work through caller cancellation and shutdown."""

        async def prepare() -> T:
            context = copy_context()

            def invoke() -> T:
                return context.run(operation)

            future = asyncio.get_running_loop().run_in_executor(None, invoke)
            self.workers.retain(session_id, cast(asyncio.Future[object], future))
            return await asyncio.shield(future)

        return await self._io.run(prepare)

    def _release(self) -> None:
        if self._io.closed and not self._io.active and self._namespace is not None:
            self._namespace.release(self)

    async def aclose(self) -> bool:
        drained = await self._io.aclose()
        return drained and not self.workers.active
