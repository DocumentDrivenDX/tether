"""Cancellation/timeout never relinquishes actual database or namespace ownership."""

import asyncio
import hashlib
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from psycopg import sql

from tether.stores.postgres import DatabaseRoot, PostgresHistory, migrate
from tether.types import Message
from tether.types.execution import CommitUnknown
from tether.types.history import HistoryRecord
from tether.types.storage import IntegrityError, StorageLimits, StoreError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("cancel", [False, True])
async def test_pending_transaction_retains_capacity_until_actual_release(database, cancel):
    h = database
    entered, release = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def source(actor, session_id):
        async with h.pool.connection() as connection:
            yield connection
        if session_id is not None:
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
    task = asyncio.create_task(
        history.put(actor=h.actor, session_id="session", key="key", record=value)
    )
    try:
        await asyncio.wait_for(entered.wait(), 2)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(CommitUnknown, match="database_commit_unknown"):
                await task
        with pytest.raises(StoreError, match="database_capacity"):
            await history.put(actor=h.actor, session_id="session", key="other", record=value)
        assert not await root.aclose()
        with pytest.raises(StoreError, match="database_root_closed"):
            await history.read([], actor=h.actor) if False else await root.run(
                h.actor, None, lambda c: c.execute("SELECT 1")
            )
    finally:
        release.set()
        await asyncio.sleep(0.02)
        assert await root.aclose()
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


async def test_read_timeout_does_not_claim_a_write_and_source_exit_is_retained(database):
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
    try:
        with pytest.raises(StoreError, match="database_unavailable"):
            await root.run(h.actor, None, operation)
        assert entered.is_set()
        assert not await root.aclose()
    finally:
        release.set()
        await asyncio.sleep(0.02)
        assert await root.aclose()


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
    assert await h.root.aclose()
    pending = asyncio.get_running_loop().create_future()
    replacement.workers.retain("session", pending)
    third = await DatabaseRoot.open(h.source, actor=h.actor, schema=h.schema)
    try:
        assert third.workers is replacement.workers
        assert third.workers.busy("session")
    finally:
        pending.set_result(None)
        await asyncio.sleep(0)
        assert await replacement.aclose()
        assert await third.aclose()


async def test_held_migration_lock_has_a_finite_wait_and_retained_release(database):
    h = database
    finished = asyncio.Event()
    lock = int.from_bytes(hashlib.sha256(h.schema.encode()).digest()[:8], signed=True)

    @asynccontextmanager
    async def source(actor, session_id):
        async with h.pool.connection() as connection:
            yield connection
        finished.set()

    async with h.pool.connection() as connection:
        await connection.execute("SELECT pg_advisory_lock(%s)", (lock,))
        try:
            with pytest.raises(CommitUnknown, match="database_commit_unknown"):
                await migrate(
                    source,
                    actor=h.actor,
                    schema=h.schema,
                    limits=StorageLimits(io_seconds=0.02, shutdown_seconds=0.01),
                )
            assert not finished.is_set()
        finally:
            await connection.execute("SELECT pg_advisory_unlock(%s)", (lock,))
    await asyncio.wait_for(finished.wait(), 2)
    assert await migrate(h.source, actor=h.actor, schema=h.schema)


async def test_canceled_root_bootstrap_releases_only_after_actual_source_exit(database):
    h = database
    entered, release, exited = asyncio.Event(), asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def source(actor, session_id):
        async with h.pool.connection() as connection:
            yield connection
        entered.set()
        await release.wait()
        exited.set()

    task = asyncio.create_task(DatabaseRoot.open(source, actor=h.actor, schema=h.schema))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not exited.is_set()
    finally:
        release.set()
        await asyncio.wait_for(exited.wait(), 2)
    sibling = await DatabaseRoot.open(h.source, actor=h.actor, schema=h.schema)
    assert sibling.workers is h.root.workers
    assert await sibling.aclose()
