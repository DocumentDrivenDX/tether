"""One-attempt preparation and physical cleanup for real async PostgreSQL pools."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from context_lifetime_controls import settle_tasks
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool


@dataclass(eq=False, slots=True)
class _PoolLoan:
    connection: AsyncConnection | None = None
    returning: bool = False


class TrackedAsyncConnectionPool(AsyncConnectionPool):
    """Observe public checkouts until the matching public return terminates."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.loans: set[_PoolLoan] = set()
        self.loans_changed = asyncio.Event()

    async def getconn(self, timeout: float | None = None) -> AsyncConnection:  # noqa: ASYNC109
        loan = _PoolLoan()
        self.loans.add(loan)
        try:
            connection = await super().getconn(timeout=timeout)
        except BaseException:
            self.loans.discard(loan)
            self.loans_changed.set()
            raise
        loan.connection = connection
        return connection

    async def _after_connection_return(self, conn: AsyncConnection) -> None:
        pass

    async def putconn(self, conn: AsyncConnection) -> None:
        loan = next(
            (
                candidate
                for candidate in self.loans
                if candidate.connection is conn and not candidate.returning
            ),
            None,
        )
        if loan is not None:
            loan.returning = True
        try:
            await super().putconn(conn)
            await self._after_connection_return(conn)
        finally:
            if loan is not None:
                self.loans.discard(loan)
                self.loans_changed.set()


class PoolReconnectFailed(RuntimeError):
    def __init__(self, stats: dict[str, int]) -> None:
        super().__init__("pool_reconnect_failed")
        self.stats = stats


class PreparedPoolOwner:
    def __init__(
        self,
        conninfo: str,
        *,
        configure: Callable[[AsyncConnection], Awaitable[None]] | None = None,
        connection_class: type[AsyncConnection] = AsyncConnection,
        pool_class: type[TrackedAsyncConnectionPool] = TrackedAsyncConnectionPool,
    ) -> None:
        self._configure_callback = configure or _configure_noop
        self.configure_failure: asyncio.Future[BaseException] = (
            asyncio.get_running_loop().create_future()
        )
        self.reconnect_failure: asyncio.Future[dict[str, int]] = (
            asyncio.get_running_loop().create_future()
        )
        self.configured_connections: list[AsyncConnection] = []
        self.failed_connections: list[AsyncConnection] = []
        self.pool_close_entered = asyncio.Event()
        self.workers: tuple[asyncio.Task[Any], ...] = ()
        self.scheduler: asyncio.Task[Any] | None = None
        self.cleanup_started = asyncio.Event()
        self._captured = False
        self._wait_task: asyncio.Task[None] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self.pool = pool_class(
            conninfo,
            min_size=8,
            max_size=8,
            timeout=3,
            kwargs={"connect_timeout": 3},
            open=False,
            configure=self._configure,
            reconnect_failed=self._reconnect_failed,
            connection_class=connection_class,
        )
        self._borrowed_connections = self.pool.loans
        self._borrowers_changed = self.pool.loans_changed

    async def _configure(self, connection: AsyncConnection) -> None:
        self.configured_connections.append(connection)
        try:
            await self._configure_callback(connection)
        except BaseException as error:
            self.failed_connections.append(connection)
            if not self.configure_failure.done():
                self.configure_failure.set_result(error)
            raise

    async def _reconnect_failed(self, pool: AsyncConnectionPool) -> None:
        if not self.reconnect_failure.done():
            self.reconnect_failure.set_result(pool.get_stats())

    def _capture_workers(self) -> None:
        if self._captured:
            return
        self.workers = tuple(self.pool._workers)
        self.scheduler = self.pool._sched_runner
        self._captured = True

    async def prepare(self) -> PreparedPoolOwner:
        try:
            await self.pool.open()
            self._capture_workers()
            self._wait_task = asyncio.create_task(self.pool.wait(timeout=3))
            done, _ = await asyncio.wait(
                (self._wait_task, self.configure_failure, self.reconnect_failure),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if self.configure_failure in done or self.configure_failure.done():
                raise self.configure_failure.result()
            if self.reconnect_failure in done or self.reconnect_failure.done():
                raise PoolReconnectFailed(self.reconnect_failure.result())
            await self._wait_task
            await self._validate_sql()
            return self
        except BaseException as primary:
            self._capture_workers()
            self._start_cleanup(primary)
            await self._await_cleanup(primary=primary)
            raise

    async def _validate_sql(self) -> None:
        async with self.connection() as connection:
            cursor = await connection.execute("SELECT 1")
            row = await cursor.fetchone()
        if row != (1,):
            raise AssertionError("prepared_pool_sql_validation_failed")

    @asynccontextmanager
    async def connection(self) -> AsyncGenerator[AsyncConnection, None]:
        async with self.pool.connection() as connection:
            yield connection

    def _start_cleanup(self, primary: BaseException | None = None) -> None:
        if self._cleanup_task is None:
            self.cleanup_started.set()
            self._cleanup_task = asyncio.create_task(self._close_and_join(primary))

    async def _close_and_join(self, primary: BaseException | None) -> None:
        errors = await self._settle_wait_task(primary)
        self.pool_close_entered.set()
        try:
            await self.pool.close()
        except BaseException as error:
            if error is not primary:
                errors.append(error)
        errors.extend(await self._join_captured_workers(primary))
        await self._wait_for_borrowers()
        errors.extend(await self._close_configured_connections(primary))
        if errors:
            self._raise_cleanup_errors(errors, primary)

    async def _settle_wait_task(self, primary: BaseException | None) -> list[BaseException]:
        if self._wait_task is None:
            return []
        if not self._wait_task.done():
            self._wait_task.cancel()
        outcomes = await asyncio.gather(self._wait_task, return_exceptions=True)
        return [
            outcome
            for outcome in outcomes
            if isinstance(outcome, BaseException)
            and not isinstance(outcome, asyncio.CancelledError)
            and outcome is not primary
        ]

    async def _join_captured_workers(self, primary: BaseException | None) -> list[BaseException]:
        owners = (*self.workers, *((self.scheduler,) if self.scheduler is not None else ()))
        if not owners:
            return []
        outcomes = await asyncio.gather(*owners, return_exceptions=True)
        return [
            outcome
            for outcome in outcomes
            if isinstance(outcome, BaseException)
            and not isinstance(outcome, asyncio.CancelledError)
            and outcome is not primary
        ]

    async def _wait_for_borrowers(self) -> None:
        while self._borrowed_connections:
            self._borrowers_changed.clear()
            if self._borrowed_connections:
                await self._borrowers_changed.wait()

    async def wait_for_borrowers(self) -> None:
        await self._wait_for_borrowers()

    async def _close_configured_connections(
        self, primary: BaseException | None
    ) -> list[BaseException]:
        errors = []
        for connection in tuple(self.configured_connections):
            if not connection.closed:
                try:
                    await connection.close()
                except BaseException as error:
                    if error is not primary:
                        errors.append(error)
        return errors

    @staticmethod
    def _raise_cleanup_errors(errors: list[BaseException], primary: BaseException | None) -> None:
        if primary is not None:
            raise BaseExceptionGroup("prepared_pool_cleanup_failed", [primary, *errors])
        raise BaseExceptionGroup("prepared_pool_cleanup_failed", errors)

    async def _await_cleanup(
        self,
        *,
        primary: BaseException | None = None,
        cancellation_acks: tuple[asyncio.Event, ...] = (),
    ) -> None:
        self._start_cleanup(primary)
        assert self._cleanup_task is not None
        cancellation = None
        try:
            await settle_tasks((self._cleanup_task,), cancellation_acks=cancellation_acks)
        except asyncio.CancelledError as error:
            cancellation = error
        await self._report_cleanup_outcome(self._cleanup_task, primary, cancellation)

    @staticmethod
    async def _report_cleanup_outcome(
        owner: asyncio.Task[None],
        primary: BaseException | None,
        cancellation: asyncio.CancelledError | None,
    ) -> None:
        cleanup_error = None
        try:
            owner.result()
        except BaseException as error:
            cleanup_error = error
        if primary is not None:
            if cleanup_error is not None:
                primary.add_note(f"pool cleanup also failed: {cleanup_error!r}")
            return
        if cancellation is not None:
            if cleanup_error is not None:
                raise BaseExceptionGroup(
                    "prepared_pool_cancel_and_cleanup_failed", [cancellation, cleanup_error]
                )
            raise cancellation
        if cleanup_error is not None:
            raise cleanup_error

    async def aclose(self, *, cancellation_acks: tuple[asyncio.Event, ...] = ()) -> None:
        self._capture_workers()
        await self._await_cleanup(cancellation_acks=cancellation_acks)

    @property
    def worker_refs_done(self) -> bool:
        return all(task.done() for task in self.workers) and (
            self.scheduler is None or self.scheduler.done()
        )


async def _configure_noop(_connection: AsyncConnection) -> None:
    return None


__all__ = ["PoolReconnectFailed", "PreparedPoolOwner"]
