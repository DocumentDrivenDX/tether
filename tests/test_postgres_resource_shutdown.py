"""Real PostgreSQL connection/worker ownership through public resource shutdown."""

import asyncio
import sys
from contextlib import asynccontextmanager
from threading import Event

import pytest
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from resource_lifetime_controls import (
    cancel_and_expect_cancellation,
    close_after_release,
    wait_closed_entry,
)
from tui_event_waits import wait_for_event_or_operation

from tether.client import HistoryRecord, Message
from tether.client.postgres import PostgresResources, StorageLimits
from tether.stores.postgres import DatabaseRoot

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_public_postgres_resources_wait_for_real_borrowed_connection_release(database):
    """@covers US-065-AC5 @covers US-068-AC8"""
    h = database
    entered, release, exited = asyncio.Event(), asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def source(actor, session_id):
        async with h.pool.connection() as connection:
            yield connection
            if session_id is not None:
                entered.set()
                await release.wait()
        if session_id is not None:
            exited.set()

    resources = await PostgresResources.open(
        source,
        actor=h.actor,
        schema=h.schema,
        limits=StorageLimits(io_seconds=1, shutdown_seconds=0.01),
    )
    operation = None
    waiter = None
    try:
        with pytest.raises(RuntimeError, match="postgres_resources_not_closing"):
            await resources.wait_closed()
        history = resources.storage.history
        operation = asyncio.create_task(
            history.put(
                actor=h.actor,
                session_id="held-session",
                key="held-content",
                record=HistoryRecord(
                    kind="message", message=Message(role="user", content="Fabricated")
                ),
            )
        )
        await wait_for_event_or_operation(entered, operation)
        await cancel_and_expect_cancellation(operation)
        assert await resources.aclose() is False
        waiter = await wait_closed_entry(resources)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not exited.is_set()
        assert not h.pool.closed
        with pytest.raises(RuntimeError, match="postgres_resources_closed"):
            _ = resources.storage
    finally:
        await close_after_release(
            resources, release.set, operation, waiter, primary=sys.exception()
        )
    assert exited.is_set()
    assert await resources.aclose()
    assert not h.pool.closed
    async with h.pool.connection() as connection:
        cursor = await connection.execute("SELECT 1")
        assert await cursor.fetchone() == (1,)


async def test_database_wait_closed_preserves_real_executor_worker_after_waiter_cancel(database):
    """@covers US-065-AC5"""
    h = database
    entered, release = asyncio.Event(), Event()
    loop = asyncio.get_running_loop()
    root = await DatabaseRoot.open(
        h.source,
        actor=h.actor,
        schema=h.schema,
        limits=StorageLimits(io_seconds=1, shutdown_seconds=0.01),
    )

    def held():
        loop.call_soon_threadsafe(entered.set)
        release.wait()
        return "Fabricated completed worker"

    operation = None
    waiter = None
    try:
        with pytest.raises(RuntimeError, match="database_root_not_closing"):
            await root.wait_closed()
        operation = asyncio.create_task(root.prepare("held-session", held))
        await wait_for_event_or_operation(entered, operation)
        await cancel_and_expect_cancellation(operation)
        assert await root.aclose() is False
        waiter = await wait_closed_entry(root)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert root.workers.busy("held-session")
    finally:
        await close_after_release(root, release.set, operation, waiter, primary=sys.exception())
    assert not root.workers.busy("held-session")
    assert not h.pool.closed
