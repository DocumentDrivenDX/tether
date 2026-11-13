"""Owned setup and teardown for the real PostgreSQL test database fixture."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from postgres_prepared_pool import PreparedPoolOwner
from psycopg import sql

from tether.stores.postgres import DatabaseRoot, PostgresHistory, migrate
from tether.stores.postgres.source import Connection
from tether.types.execution import Actor

RootOpener = Callable[..., Awaitable[DatabaseRoot]]
Migration = Callable[..., Awaitable[Any]]


@dataclass(eq=False, slots=True)
class _SourceInvocation:
    task: asyncio.Task[Any]
    call_index: int


@dataclass
class DatabaseFixtureControl:
    hold_source_call: int | None = None
    source_entered: asyncio.Event = field(default_factory=asyncio.Event)
    source_release: asyncio.Event = field(default_factory=asyncio.Event)
    setup_cancel_ack: asyncio.Event = field(default_factory=asyncio.Event)
    cleanup_started: asyncio.Event = field(default_factory=asyncio.Event)
    schema: str | None = None
    owner: DatabaseFixtureOwner | None = None


class _TrackedSource:
    def __init__(
        self,
        owner: DatabaseFixtureOwner,
        control: DatabaseFixtureControl | None,
    ) -> None:
        self.owner = owner
        self.control = control
        self.calls: list[tuple[Actor, str | None]] = []
        self._invocations: set[_SourceInvocation] = set()
        self._changed = asyncio.Event()

    def __call__(
        self, caller: Actor, session_id: str | None, /
    ) -> AbstractAsyncContextManager[Connection]:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("database_source_requires_async_task")
        self.calls.append((caller, session_id))
        invocation = _SourceInvocation(task, len(self.calls))
        self._invocations.add(invocation)
        task.add_done_callback(lambda done: self._finish_task(done))
        return self._connection(invocation)

    def _finish_task(self, task: asyncio.Task[Any]) -> None:
        self._invocations = {
            invocation for invocation in self._invocations if invocation.task is not task
        }
        self._changed.set()

    async def aclose(self) -> bool:
        return not self._invocations

    async def wait_closed(self) -> bool:
        await self.drain(None)
        return True

    @asynccontextmanager
    async def _connection(self, invocation: _SourceInvocation) -> AsyncIterator[Connection]:
        async with self.owner.pool.pool.connection() as connection:
            if self._should_hold(invocation):
                assert self.control is not None
                self.control.source_entered.set()
                await self.control.source_release.wait()
            yield connection

    def _should_hold(self, invocation: _SourceInvocation) -> bool:
        control = self.control
        if control is None or control.hold_source_call is None:
            return False
        return invocation.call_index == control.hold_source_call

    def _tasks(self) -> tuple[asyncio.Task[Any], ...]:
        return tuple(invocation.task for invocation in self._invocations)

    async def drain(self, primary: BaseException | None) -> list[BaseException]:
        errors: list[BaseException] = []
        while self._invocations:
            tasks = tuple(dict.fromkeys(self._tasks()))
            if not tasks:
                await self._changed.wait()
                continue
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            errors.extend(
                outcome
                for outcome in outcomes
                if isinstance(outcome, BaseException)
                and not isinstance(outcome, asyncio.CancelledError)
                and outcome is not primary
            )
            self._invocations = {
                invocation for invocation in self._invocations if not invocation.task.done()
            }
        return errors


class DatabaseFixtureOwner:
    def __init__(
        self,
        conninfo: str,
        schema: str,
        actor: Actor,
        control: DatabaseFixtureControl | None,
        *,
        migration: Migration,
        root_open: RootOpener,
    ) -> None:
        self.schema, self.actor, self.control = schema, actor, control
        self.pool = PreparedPoolOwner(conninfo)
        self.source = _TrackedSource(self, control)
        self.root: DatabaseRoot | None = None
        self.migration, self.root_open = migration, root_open
        self._setup_tasks: list[asyncio.Task[Any]] = []
        self._cleanup_task: asyncio.Task[None] | None = None

    async def open(self) -> SimpleNamespace:
        await self.pool.prepare()
        await self._setup_step(self.migration(self.source, actor=self.actor, schema=self.schema))
        await self._setup_step(self._open_root())
        assert self.root is not None
        return SimpleNamespace(
            pool=self.pool.pool,
            source=self.source,
            root=self.root,
            schema=self.schema,
            actor=self.actor,
            calls=self.source.calls,
            history=PostgresHistory(self.root),
        )

    async def _open_root(self) -> DatabaseRoot:
        root = await self.root_open(self.source, actor=self.actor, schema=self.schema)
        self.root = root
        return root

    async def _setup_step(self, operation: Awaitable[Any]) -> Any:
        async def run_step() -> Any:
            return await operation

        task: asyncio.Task[Any] = asyncio.create_task(run_step())
        self._setup_tasks.append(task)
        return await _await_retained(task, self.control)

    async def aclose(self, primary: BaseException | None = None) -> None:
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._close_and_drop(primary))
        await _await_retained(self._cleanup_task, self.control)

    async def _close_and_drop(self, primary: BaseException | None) -> None:
        if self.control is not None:
            self.control.cleanup_started.set()
        errors: list[BaseException] = []
        root_drained = await self._close_root(errors, primary)
        errors.extend(await self.source.drain(primary))
        loans_drained = await self._drain_pool_loans(errors)
        if root_drained and loans_drained:
            await self._drop_schema(errors)
        try:
            await self.pool.aclose()
        except BaseException as error:
            if error is not primary:
                errors.append(error)
        if errors:
            raise BaseExceptionGroup("postgres_fixture_cleanup_failed", errors)

    async def _close_root(self, errors: list[BaseException], primary: BaseException | None) -> bool:
        root = self.root
        if root is None:
            return True
        try:
            await root.aclose()
        except BaseException as error:
            if error is not primary:
                errors.append(error)
        try:
            return await root.wait_closed()
        except BaseException as error:
            if error is not primary:
                errors.append(error)
            return False

    async def _drain_pool_loans(self, errors: list[BaseException]) -> bool:
        try:
            await self.pool.wait_for_borrowers()
        except BaseException as error:
            errors.append(error)
            return False
        return True

    async def _drop_schema(self, errors: list[BaseException]) -> None:
        try:
            async with self.pool.pool.connection() as connection:
                await connection.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(self.schema))
                )
        except BaseException as error:
            errors.append(error)


async def _await_retained(task: asyncio.Task[Any], control: DatabaseFixtureControl | None) -> Any:
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            if cancellation is None:
                cancellation = error
                if control is not None:
                    control.setup_cancel_ack.set()
        except BaseException:
            if not task.done():
                raise
    try:
        result = task.result()
    except BaseException as operation_error:
        if cancellation is not None and operation_error is not cancellation:
            raise BaseExceptionGroup(
                "postgres_setup_and_cancellation_failed", [cancellation, operation_error]
            ) from cancellation
        raise
    if cancellation is not None:
        raise cancellation
    return result


@asynccontextmanager
async def database_context(
    conninfo: str,
    *,
    control: DatabaseFixtureControl | None = None,
    migration: Migration = migrate,
    root_open: RootOpener = DatabaseRoot.open,
) -> AsyncIterator[SimpleNamespace]:
    schema = "test_" + uuid4().hex
    actor = Actor(id="fabricated-owner")
    owner = DatabaseFixtureOwner(
        conninfo,
        schema,
        actor,
        control,
        migration=migration,
        root_open=root_open,
    )
    if control is not None:
        control.schema, control.owner = schema, owner
    primary: BaseException | None = None
    try:
        yield await owner.open()
    except BaseException as error:
        primary = error
        raise
    finally:
        cleanup = asyncio.create_task(owner.aclose(primary))
        try:
            await _await_retained(cleanup, control)
        except BaseException as cleanup_error:
            if primary is None:
                raise
            if cleanup_error is not primary:
                primary.add_note(f"postgres fixture cleanup also failed: {cleanup_error!r}")


__all__ = ["DatabaseFixtureControl", "database_context"]
