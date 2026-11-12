"""Cancellation/timeout never relinquishes actual database or namespace ownership."""

import asyncio
import hashlib
import sys
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from context_lifetime_controls import settle_tasks
from postgres_pending_io_controls import (
    FailingUnlockHolder,
    PendingIOControl,
    cancellation_resistant_wait,
    close_roots_and_drain,
    migration_source,
    retain_cleanup,
    unlock_and_settle_migration,
    wait_event_or_task,
    wait_for_advisory_wait,
)
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from psycopg import sql
from resource_lifetime_controls import cancel_and_expect_cancellation, close_after_release

from tether.stores.postgres import DatabaseRoot, PostgresHistory, migrate
from tether.types import Message
from tether.types.execution import CommitUnknown
from tether.types.history import HistoryRecord
from tether.types.storage import IntegrityError, StorageLimits, StoreError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def assert_capacity_and_closed_source(root, history, actor, value, calls):
    with pytest.raises(StoreError, match="database_capacity"):
        await history.put(actor=actor, session_id="session", key="other", record=value)
    assert not await root.aclose()
    before = len(calls)
    with pytest.raises(StoreError, match="database_root_closed"):
        await root.run(actor, None, lambda connection: connection.execute("SELECT 1"))
    assert len(calls) == before


@pytest.mark.parametrize("cancel", [False, True])
async def test_pending_transaction_retains_capacity_until_actual_release(
    database, cancel, monkeypatch
):
    h = database
    entered, release = asyncio.Event(), asyncio.Event()
    actual = None

    @asynccontextmanager
    async def source(actor, session_id):
        nonlocal actual
        async with h.pool.connection() as connection:
            yield connection
        if session_id is not None:
            actual = asyncio.current_task()
            entered.set()
            await release.wait()

    root = await DatabaseRoot.open(
        source,
        actor=h.actor,
        schema=h.schema,
        limits=StorageLimits(io_seconds=0.03, shutdown_seconds=0.01, max_pending_io=1),
    )
    value = HistoryRecord(kind="message", message=Message(role="user", content="Fabricated"))
    history = PostgresHistory(root)
    task = None
    try:
        control = PendingIOControl()
        control.install(monkeypatch)
        task = asyncio.create_task(
            history.put(actor=h.actor, session_id="session", key="key", record=value)
        )
        await wait_event_or_task(entered, task)
        assert actual is not None
        assert not actual.done()
        assert control.durations == (0.03,)
        if cancel:
            await cancel_and_expect_cancellation(task)
        else:
            timer = control.arm_next(0.03)
            with pytest.raises(CommitUnknown, match="database_commit_unknown"):
                await task
            assert timer.expired()
        await assert_capacity_and_closed_source(root, history, h.actor, value, h.calls)
    finally:
        await close_after_release(root, release.set, task, primary=sys.exception())
        assert actual is None or actual.done()
    ref = await h.history.put(actor=h.actor, session_id="session", key="key", record=value)
    assert (await h.history.read([ref], actor=h.actor))[0].record == value
    assert not h.pool.closed


@pytest.mark.parametrize("operation", ["read", "close", "history_read"])
async def test_database_root_refuses_wrong_loop_before_source_or_mutation(database, operation):
    h = database
    ref = await h.history.put(
        actor=h.actor,
        session_id="session",
        key="key",
        record=HistoryRecord(kind="summary", summary="Fabricated"),
    )
    before = len(h.calls)

    async def invoke():
        if operation == "history_read":
            await h.history.read([ref], actor=h.actor)
        elif operation == "close":
            await h.root.aclose()
        else:
            await h.root.run(h.actor, None, lambda c: c.execute("SELECT 1"))

    def foreign():
        with pytest.raises(RuntimeError, match="wrong_event_loop"):
            asyncio.run(invoke())

    await asyncio.to_thread(foreign)
    assert len(h.calls) == before
    assert await h.root.run(h.actor, None, lambda c: c.execute("SELECT 1"))


async def test_every_acquisition_verifies_namespace_and_durability(database):
    h = database
    async with h.pool.connection() as connection:
        await connection.execute(
            sql.SQL("UPDATE {}.metadata SET namespace=%s").format(sql.Identifier(h.schema)),
            (uuid4(),),
        )
    with pytest.raises(IntegrityError, match="database_namespace_mismatch"):
        await h.root.run(h.actor, None, lambda c: c.execute("SELECT 1"))

    @asynccontextmanager
    async def weak(actor, session_id):
        async with h.pool.connection() as connection:
            await connection.set_autocommit(True)
            await connection.execute("SET synchronous_commit=off")
            try:
                yield connection
            finally:
                await connection.execute("SET synchronous_commit=on")
                await connection.set_autocommit(False)

    with pytest.raises(IntegrityError, match="unsupported_database_durability"):
        await DatabaseRoot.open(weak, actor=h.actor, schema=h.schema)


async def test_read_timeout_does_not_claim_a_write_and_source_exit_is_retained(
    database, monkeypatch
):
    h = database
    entered, release = asyncio.Event(), asyncio.Event()

    async def operation(connection):
        entered.set()
        await release.wait()
        return await connection.execute("SELECT 1")

    root = await DatabaseRoot.open(
        h.source,
        actor=h.actor,
        schema=h.schema,
        limits=StorageLimits(io_seconds=0.02, shutdown_seconds=0.01),
    )
    task = None
    try:
        control = PendingIOControl()
        control.install(monkeypatch)

        task = asyncio.create_task(root.run(h.actor, None, operation))
        await wait_event_or_task(entered, task)
        assert control.durations == (0.02,)
        timer = control.arm_next(0.02)
        with pytest.raises(StoreError, match="database_unavailable"):
            await task
        assert timer.expired()
        assert entered.is_set()
        actual = next(iter(root._io.active))
        assert not actual.done()
        assert not await root.aclose()
    finally:
        await close_after_release(root, release.set, task, primary=sys.exception())


async def test_migration_source_release_failure_keeps_uncertain_commit(database):
    h = database

    @asynccontextmanager
    async def source(actor, session_id):
        async with h.pool.connection() as connection:
            yield connection
        raise RuntimeError("fabricated_release_failure")

    with pytest.raises(CommitUnknown, match="database_migration_unknown"):
        await migrate(source, actor=h.actor, schema=h.schema)
    assert await migrate(h.source, actor=h.actor, schema=h.schema)


async def test_repeated_old_close_cannot_detach_replacement_workers(database):
    h = database
    assert await h.root.aclose()
    replacement = await DatabaseRoot.open(h.source, actor=h.actor, schema=h.schema)
    third = None
    pending = None
    try:
        assert await h.root.aclose()
        pending = asyncio.get_running_loop().create_future()
        replacement.workers.retain("session", pending)
        third = await DatabaseRoot.open(h.source, actor=h.actor, schema=h.schema)
        assert third.workers.active is replacement.workers.active
        assert third.workers.busy("session")
    finally:

        async def teardown():
            if pending is not None and not pending.done():
                pending.set_result(None)
            roots = (replacement,) if third is None else (replacement, third)
            assert all(await close_roots_and_drain(roots))

        await retain_cleanup(teardown(), primary=sys.exception())


async def test_held_migration_lock_has_a_finite_wait_and_retained_release(database, monkeypatch):
    h = database
    source_entered, source_finished = asyncio.Event(), asyncio.Event()
    source, observed = migration_source(h, source_entered, source_finished)
    lock = int.from_bytes(hashlib.sha256(h.schema.encode()).digest()[:8], signed=True)

    async with h.pool.connection() as connection:
        cursor = await connection.execute("SELECT pg_backend_pid()")
        holder_pid = (await cursor.fetchone())[0]
        await connection.commit()
        await connection.execute("SELECT pg_advisory_lock(%s)", (lock,))
        await connection.commit()
        control = PendingIOControl()
        control.install(monkeypatch)
        migration = None
        actual_migration = None
        try:
            migration = asyncio.create_task(
                migrate(
                    source,
                    actor=h.actor,
                    schema=h.schema,
                    limits=StorageLimits(io_seconds=0.02, shutdown_seconds=0.01),
                )
            )
            await wait_event_or_task(source_entered, migration)
            waiter_pid = observed["pid"]
            actual_migration = observed["task"]
            assert actual_migration is not None
            await wait_for_advisory_wait(h, lock, holder_pid, waiter_pid, migration)
            assert control.durations == (0.02,)
            timer = control.arm_next(0.02)
            with pytest.raises(CommitUnknown, match="database_commit_unknown"):
                await migration
            assert timer.expired()
            assert not source_finished.is_set()
        finally:
            await retain_cleanup(
                unlock_and_settle_migration(
                    connection,
                    lock,
                    migration,
                    observed.get("task"),
                    source_finished,
                ),
                primary=sys.exception(),
            )
    assert await migrate(h.source, actor=h.actor, schema=h.schema)


async def test_failed_migration_unlock_closes_holder_and_retains_source_until_exit():
    unlock_error = RuntimeError("fabricated_unlock_failure")
    source_entered, release, source_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    caller_entered, caller_release, caller_cancel_ack = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    close_entered, allow_close = asyncio.Event(), asyncio.Event()

    async def actual_source():
        source_entered.set()
        try:
            await release.wait()
        finally:
            source_finished.set()

    holder = FailingUnlockHolder(unlock_error, close_entered, allow_close, release)
    actual = asyncio.create_task(actual_source())
    caller = asyncio.create_task(
        cancellation_resistant_wait(caller_entered, caller_cancel_ack, caller_release)
    )
    cleanup = None
    try:
        await wait_event_or_task(source_entered, actual)
        await wait_event_or_task(caller_entered, caller)
        cleanup = asyncio.create_task(
            unlock_and_settle_migration(holder, 9, caller, actual, source_finished)
        )
        await wait_event_or_task(close_entered, cleanup)
        await wait_event_or_task(caller_cancel_ack, cleanup)
        assert holder.closed
        assert not source_finished.is_set()
        assert not actual.done()
        allow_close.set()
        await wait_event_or_task(source_finished, cleanup)
        assert not cleanup.done()
        caller_release.set()
        with pytest.raises(BaseExceptionGroup) as caught:
            await cleanup
        assert caught.value.exceptions == (unlock_error,)
        assert source_finished.is_set()
    finally:
        allow_close.set()
        release.set()
        caller_release.set()
        await settle_tasks((caller, actual, *((cleanup,) if cleanup is not None else ())))


async def test_canceled_root_bootstrap_releases_only_after_actual_source_exit(
    database, monkeypatch
):
    h = database
    entered, release, exited = asyncio.Event(), asyncio.Event(), asyncio.Event()
    actual = None

    @asynccontextmanager
    async def source(actor, session_id):
        nonlocal actual
        async with h.pool.connection() as connection:
            yield connection
        actual = asyncio.current_task()
        entered.set()
        await release.wait()
        exited.set()

    task = None
    try:
        control = PendingIOControl()
        control.install(monkeypatch)
        task = asyncio.create_task(DatabaseRoot.open(source, actor=h.actor, schema=h.schema))
        await wait_event_or_task(entered, task)
        assert control.durations == (30.0,)
        await cancel_and_expect_cancellation(task)
        assert not exited.is_set()
    finally:

        async def teardown():
            release.set()
            if task is not None and not task.done():
                task.cancel()
            await asyncio.gather(
                *((task,) if task is not None else ()),
                *((actual,) if actual is not None else ()),
                return_exceptions=True,
            )
            assert exited.is_set()

        await retain_cleanup(teardown(), primary=sys.exception())
    sibling = await DatabaseRoot.open(h.source, actor=h.actor, schema=h.schema)
    assert sibling.workers.active is h.root.workers.active
    assert await sibling.aclose()
