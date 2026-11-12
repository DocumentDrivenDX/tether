"""Canonical capture is bounded; reconstruction owns actual work outside database locks."""

import asyncio
import sys
from threading import Event
from uuid import uuid4

import pytest
from postgres_pending_io_controls import (
    close_root_settle_and_finalize,
    retain_cleanup,
    wait_event_or_task,
)
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from psycopg import sql
from resource_lifetime_controls import cancel_and_expect_cancellation
from test_postgres_leases import expire
from test_postgres_read_integrity import snapshot
from test_postgres_restore_primitives import pending_values

from tether.stores.postgres import DatabaseRoot
from tether.stores.postgres import capture as capture_module
from tether.stores.postgres.capture import Capture
from tether.types.canonical import canonical
from tether.types.execution import NewEntry, WorkerOutstanding
from tether.types.storage import (
    IntegrityError,
    LeaseLost,
    SessionInit,
    SessionSummary,
    StorageLimits,
    StoreError,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def seed(h):
    store = Capture(h.root, h.actor)
    summary = await store.create(SessionInit(ref="fabricated"), str(uuid4()))
    token, _ = await store.acquire(summary.id, turn_id="job")
    values, _ = pending_values(summary, h.actor)
    entries = await store.append_fenced(token, values)
    return token, entries


async def test_reconstruct_preserves_pending_unknowns_and_all_original_facts(database):
    h = database
    token, entries = await seed(h)
    capture = Capture(h.root, h.actor)
    index = await capture.reconstruct(token)
    assert tuple(index.entries) == entries
    assert index.summary.pending
    assert index.summary.unresolved_effect_count == 1
    assert len(index.pending) == 2
    assert len(index.effects) == 1
    assert index.summary.entry_count == len(entries)
    assert not h.root.workers.busy(token.session_id)
    await capture.release(token)


@pytest.mark.parametrize(("page_limit", "success"), [(100, True), (2, False)])
async def test_capture_batch_limit_counts_database_batches_not_an_extra_completion_iteration(
    database, page_limit, success
):
    h = database
    token, _ = await seed(h)
    root = await DatabaseRoot.open(
        h.source,
        actor=h.actor,
        schema=h.schema,
        limits=StorageLimits(page_limit=page_limit, max_startup_batches=1),
    )
    try:
        capture = Capture(root, h.actor)
        if success:
            assert (await capture.reconstruct(token)).summary.entry_count == 10
        else:
            with pytest.raises(StoreError, match="turn_snapshot_batches"):
                await capture.reconstruct(token)
    finally:
        assert await root.aclose()
        await Capture(h.root, h.actor).release(token)


async def test_empty_capture_still_charges_canonical_frontier_bytes(database):
    h = database
    capture = Capture(h.root, h.actor)
    summary = await capture.create(SessionInit(), str(uuid4()))
    token, _ = await capture.acquire(summary.id, turn_id="job")
    root = await DatabaseRoot.open(
        h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(max_index_bytes=1)
    )
    try:
        with pytest.raises(StoreError, match="turn_snapshot_bytes"):
            await Capture(root, h.actor).reconstruct(token)
    finally:
        assert await root.aclose()
        await capture.release(token)


@pytest.mark.parametrize(
    "corruption", ["count", "owner_sequence", "noncanonical_entry", "duplicate_key"]
)
async def test_reconstruction_refuses_derivative_or_journal_drift(database, corruption):
    h = database
    capture = Capture(h.root, h.actor)
    summary = await capture.create(SessionInit(), str(uuid4()))
    token, _ = await capture.acquire(summary.id, turn_id="job")
    entries = await capture.append_fenced(
        token, [NewEntry(kind="message", key="original"), NewEntry(kind="message")]
    )
    async with h.pool.connection() as connection:
        if corruption in {"count", "owner_sequence"}:
            cursor = await connection.execute(
                sql.SQL("SELECT summary FROM {}.sessions WHERE owner=%s AND id=%s").format(
                    sql.Identifier(h.schema)
                ),
                (h.actor.id.encode(), summary.id.encode()),
            )
            value = SessionSummary.model_validate_json((await cursor.fetchone())[0])
            value = value.model_copy(
                update={"message_count": 999} if corruption == "count" else {"last_seq": 999}
            )
            await connection.execute(
                sql.SQL("UPDATE {}.sessions SET summary=%s WHERE owner=%s AND id=%s").format(
                    sql.Identifier(h.schema)
                ),
                (
                    canonical(value.model_dump(mode="json")),
                    h.actor.id.encode(),
                    summary.id.encode(),
                ),
            )
        else:
            changed = (
                b" " + canonical(entries[0].model_dump(mode="json"))
                if corruption == "noncanonical_entry"
                else canonical(
                    entries[1].model_copy(update={"key": "original"}).model_dump(mode="json")
                )
            )
            await connection.execute(
                sql.SQL(
                    "UPDATE {}.entries SET envelope=%s "
                    "WHERE owner=%s AND session=%s AND position=%s"
                ).format(sql.Identifier(h.schema)),
                (
                    changed,
                    h.actor.id.encode(),
                    summary.id.encode(),
                    0 if corruption == "noncanonical_entry" else 1,
                ),
            )
    before = await snapshot(h)
    with pytest.raises(IntegrityError):
        await capture.reconstruct(token)
    assert await snapshot(h) == before


async def test_reconstruction_releases_database_resources_and_cannot_revive_expired_lease(
    database, monkeypatch
):
    h = database
    token, _ = await seed(h)
    started, release = asyncio.Event(), Event()
    loop = asyncio.get_running_loop()
    capture = Capture(h.root, h.actor)
    task = None
    actual = None
    try:
        original = capture_module.rebuild

        def delayed(captured):
            loop.call_soon_threadsafe(started.set)
            release.wait()
            return original(captured)

        monkeypatch.setattr(capture_module, "rebuild", delayed)
        task = asyncio.create_task(capture.reconstruct(token))
        await wait_event_or_task(started, task)
        actual = next(iter(h.root.workers.active))
        assert not actual.done()
        # A different connection can update the row while pure reconstruction is running.
        await capture.heartbeat(token)
        await expire(h, token.session_id)
        release.set()
        with pytest.raises(LeaseLost):
            await task
    finally:

        async def teardown():
            release.set()
            await asyncio.gather(
                *((task,) if task is not None else ()),
                *((actual,) if actual is not None else ()),
                return_exceptions=True,
            )
            await capture.release(token)

        await retain_cleanup(teardown(), primary=sys.exception())


async def test_canceled_preparation_retains_actual_future_and_namespace_until_exit(
    database, monkeypatch
):
    h = database
    token, _ = await seed(h)
    root = None
    capture = None
    task = None
    actual = None
    started, release = asyncio.Event(), Event()
    loop = asyncio.get_running_loop()
    try:
        root = await DatabaseRoot.open(
            h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(shutdown_seconds=0.01)
        )
        capture = Capture(root, h.actor)
        original = capture_module.rebuild

        def delayed(captured):
            loop.call_soon_threadsafe(started.set)
            release.wait()
            return original(captured)

        monkeypatch.setattr(capture_module, "rebuild", delayed)
        task = asyncio.create_task(capture.reconstruct(token))
        await wait_event_or_task(started, task)
        actual = next(iter(root.workers.active))
        await cancel_and_expect_cancellation(task)
        assert not await root.aclose()
        await expire(h, token.session_id)
        with pytest.raises(WorkerOutstanding):
            await Capture(h.root, h.actor).acquire(token.session_id, turn_id="replacement")
        release.set()
        await asyncio.shield(actual)
        replacement, _ = await Capture(h.root, h.actor).acquire(
            token.session_id, turn_id="replacement"
        )
        await Capture(h.root, h.actor).release(replacement)
    finally:

        async def teardown():
            release.set()
            if root is not None:
                await close_root_settle_and_finalize(
                    root,
                    (task,),
                    lambda: Capture(h.root, h.actor).release(token),
                )
            else:
                await Capture(h.root, h.actor).release(token)

        await retain_cleanup(teardown(), primary=sys.exception())
