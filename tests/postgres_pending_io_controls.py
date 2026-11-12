"""Test-local PendingIO timeout capture without changing the event loop clock."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from context_lifetime_controls import settle_tasks, wait_event_or_task


@dataclass
class _CapturedWait:
    seconds: float
    timer: asyncio.Timeout
    armed: bool = False


class _AsyncioFacade:
    def __init__(self, owner: PendingIOControl) -> None:
        self.owner = owner

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)

    async def wait_for(
        self,
        awaitable: Awaitable[Any],
        timeout: float,  # noqa: ASYNC109
    ) -> Any:
        captured = _CapturedWait(timeout, asyncio.Timeout(None))
        self.owner.waits.append(captured)
        async with captured.timer:
            return await awaitable

    async def wait(
        self,
        futures,
        *,
        timeout=None,  # noqa: ASYNC109
        return_when=asyncio.ALL_COMPLETED,
    ):
        if self.owner.wait_entered is not None:
            self.owner.wait_entered.set()
        return await asyncio.wait(futures, timeout=timeout, return_when=return_when)


class PendingIOControl:
    """Capture an actual PendingIO deadline and arm it at the test's held phase."""

    def __init__(self) -> None:
        self.waits: list[_CapturedWait] = []
        self.wait_entered: asyncio.Event | None = None
        self.facade = _AsyncioFacade(self)

    def install(self, monkeypatch: Any) -> None:
        from tether.stores.postgres import io as io_module

        monkeypatch.setattr(io_module, "asyncio", self.facade)

    def arm_next(self, seconds: float) -> asyncio.Timeout:
        captured = next((wait for wait in self.waits if not wait.armed), None)
        if captured is None or captured.seconds != seconds:
            actual = tuple(wait.seconds for wait in self.waits)
            raise AssertionError(f"pending_io_timeout_mismatch: {actual!r}")
        captured.armed = True
        captured.timer.reschedule(asyncio.get_running_loop().time() + seconds)
        return captured.timer

    def observe_wait(self, entered: asyncio.Event) -> None:
        self.wait_entered = entered

    @property
    def durations(self) -> tuple[float, ...]:
        return tuple(wait.seconds for wait in self.waits)


class _ClosableRoot(Protocol):
    async def aclose(self) -> bool: ...

    async def wait_closed(self) -> bool: ...


class FailingUnlockHolder:
    def __init__(
        self,
        error: BaseException,
        close_entered: asyncio.Event,
        close_release: asyncio.Event,
        source_release: asyncio.Event,
    ) -> None:
        self.error = error
        self.close_entered = close_entered
        self.close_release = close_release
        self.source_release = source_release
        self.closed = False

    async def execute(self, *_args: Any, **_kwargs: Any) -> None:
        raise self.error

    async def close(self) -> None:
        self.closed = True
        self.close_entered.set()
        await self.close_release.wait()
        self.source_release.set()


async def cancellation_resistant_wait(
    entered: asyncio.Event, cancel_ack: asyncio.Event, release: asyncio.Event
) -> None:
    entered.set()
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        cancel_ack.set()
        await release.wait()
        raise


async def close_root_and_drain(root: _ClosableRoot) -> bool:
    errors: list[BaseException] = []
    closed = False
    try:
        closed = await root.aclose()
    except BaseException as error:
        errors.append(error)
    try:
        drained = await root.wait_closed()
    except BaseException as error:
        errors.append(error)
    else:
        if not drained:
            errors.append(AssertionError("database_root_not_drained"))
    if errors:
        raise BaseExceptionGroup("root_close_and_drain_failed", errors)
    return closed


async def close_root_settle_and_finalize(
    root: _ClosableRoot,
    tasks: tuple[asyncio.Future[Any] | None, ...],
    finalize: Callable[[], Awaitable[Any]] | None = None,
) -> None:
    errors: list[BaseException] = []
    owned = tuple(task for task in tasks if task is not None)
    for task in owned:
        if not task.done():
            task.cancel()
    await asyncio.gather(*owned, return_exceptions=True)
    try:
        await close_root_and_drain(root)
    except BaseException as error:
        errors.append(error)
    if finalize is not None:
        try:
            await finalize()
        except BaseException as error:
            errors.append(error)
    if errors:
        raise BaseExceptionGroup("root_finalize_cleanup_failed", errors)


async def release_settle_future_and_close_root(
    root: _ClosableRoot | None,
    future: asyncio.Future[Any] | None,
    release: Callable[[], None],
    finalize: Callable[[], Awaitable[Any]] | None = None,
) -> None:
    release()
    errors: list[BaseException] = []
    if future is not None:
        outcomes = await asyncio.gather(future, return_exceptions=True)
        errors.extend(outcome for outcome in outcomes if isinstance(outcome, BaseException))
    if root is not None:
        try:
            await close_root_and_drain(root)
        except BaseException as error:
            errors.append(error)
    if finalize is not None:
        try:
            await finalize()
        except BaseException as error:
            errors.append(error)
    if errors:
        raise BaseExceptionGroup("future_root_cleanup_failed", errors)


async def close_roots_and_drain(roots: tuple[_ClosableRoot, ...]) -> tuple[bool, ...]:
    errors: list[BaseException] = []
    for root in roots:
        try:
            await root.aclose()
        except BaseException as error:
            errors.append(error)
    for root in roots:
        try:
            await root.wait_closed()
        except BaseException as error:
            errors.append(error)
    closed: list[bool] = []
    for root in roots:
        try:
            closed.append(await root.aclose())
        except BaseException as error:
            errors.append(error)
    if errors:
        raise BaseExceptionGroup("roots_close_and_drain_failed", errors)
    return tuple(closed)


def migration_source(h, entered: asyncio.Event, finished: asyncio.Event):
    observed: dict[str, Any] = {}

    @asynccontextmanager
    async def source(actor, session_id):
        observed["task"] = asyncio.current_task()
        try:
            async with h.pool.connection() as connection:
                cursor = await connection.execute("SELECT pg_backend_pid()")
                observed["pid"] = (await cursor.fetchone())[0]
                await connection.commit()
                entered.set()
                yield connection
        finally:
            finished.set()

    return source, observed


async def wait_for_advisory_wait(h, lock, holder_pid, waiter_pid, owner) -> None:
    unsigned = lock & ((1 << 64) - 1)
    class_id, object_id = unsigned >> 32, unsigned & 0xFFFFFFFF
    expected = {holder_pid: True, waiter_pid: False}
    while True:
        if owner.done():
            owner.result()
            raise AssertionError("migration_finished_before_lock_wait")
        async with h.pool.connection() as observer:
            cursor = await observer.execute(
                "SELECT pid,granted FROM pg_locks WHERE locktype='advisory' "
                "AND database=(SELECT oid FROM pg_database WHERE datname=current_database()) "
                "AND classid=%s AND objid=%s AND objsubid=1 AND pid IN (%s,%s)",
                (class_id, object_id, holder_pid, waiter_pid),
            )
            state = {pid: granted for pid, granted in await cursor.fetchall()}
        if state == expected:
            return


async def unlock_and_settle_migration(
    connection,
    lock: int,
    caller: asyncio.Future[Any] | None,
    actual: asyncio.Future[Any] | None,
    finished: asyncio.Event,
) -> None:
    errors: list[BaseException] = []
    caller_was_done = caller is not None and caller.done()
    if caller is not None and not caller_was_done:
        caller.cancel()
    try:
        await connection.execute("SELECT pg_advisory_unlock(%s)", (lock,))
    except BaseException as error:
        errors.append(error)
        try:
            # The lock is session-scoped: closing this exact holder connection
            # releases it even when the explicit unlock failed.
            await connection.close()
        except BaseException as close_error:
            errors.append(close_error)
    owners = tuple(dict.fromkeys(task for task in (caller, actual) if task is not None))
    results = await asyncio.gather(*owners, return_exceptions=True)
    for owner, result in zip(owners, results, strict=True):
        if owner is caller and (caller_was_done or isinstance(result, asyncio.CancelledError)):
            continue
        if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
            errors.append(result)
    if not finished.is_set():
        errors.append(AssertionError("migration_source_did_not_exit"))
    if errors:
        raise BaseExceptionGroup("migration_unlock_and_drain_failed", errors)


def gate_admitted_operation(
    root: Any, monkeypatch: Any, admitted: asyncio.Event, release: asyncio.Event
) -> None:
    original_run = root._io.run

    async def gated_run(operation, *, write: bool = False):
        async def admitted_operation():
            admitted.set()
            await release.wait()
            return await operation()

        return await original_run(admitted_operation, write=write)

    monkeypatch.setattr(root._io, "run", gated_run)


async def retain_cleanup(
    cleanup: Coroutine[Any, Any, Any], *, primary: BaseException | None = None
) -> None:
    owner = asyncio.create_task(cleanup)
    try:
        await settle_tasks((owner,))
        owner.result()
    except BaseException as error:
        if primary is None:
            raise
        primary.add_note(f"cleanup also failed: {error!r}")


__all__ = [
    "FailingUnlockHolder",
    "PendingIOControl",
    "cancellation_resistant_wait",
    "close_root_and_drain",
    "close_root_settle_and_finalize",
    "close_roots_and_drain",
    "gate_admitted_operation",
    "migration_source",
    "release_settle_future_and_close_root",
    "retain_cleanup",
    "unlock_and_settle_migration",
    "wait_event_or_task",
    "wait_for_advisory_wait",
]
