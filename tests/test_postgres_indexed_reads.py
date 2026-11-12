"""Bounded indexed SQL reads preserve original owner, facts and snapshot order."""

import asyncio
import sys
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest
from context_lifetime_controls import wait_event_or_task
from postgres_pending_io_controls import (
    close_root_and_drain,
    close_root_settle_and_finalize,
    retain_cleanup,
)
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from psycopg import sql
from test_postgres_primitives import append, contents
from test_postgres_restore_primitives import pending_values

from tether.stores.postgres import DatabaseRoot
from tether.stores.postgres.journal import Journal
from tether.stores.postgres.reads import Reads
from tether.types.canonical import canonical
from tether.types.execution import Actor, HistoryRef, NewEntry
from tether.types.history import HistoryLink
from tether.types.storage import (
    IntegrityError,
    SessionInit,
    StorageLimits,
    StoreError,
    UnknownSession,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class SnapshotSource:
    def __init__(self, pool: Any) -> None:
        self.pool = pool
        self.reader_pid: int | None = None
        self.writer_pid: int | None = None
        self.reader_entered = asyncio.Event()
        self.writer_entered = asyncio.Event()

    @asynccontextmanager
    async def __call__(self, actor, session_id):
        async with self.pool.connection() as connection:
            if session_id is not None:
                if self.reader_pid is None:
                    self.reader_pid = connection.info.backend_pid
                    self.reader_entered.set()
                else:
                    self.writer_pid = connection.info.backend_pid
                    self.writer_entered.set()
            yield connection


class HeldSnapshotView:
    def __init__(self, original: Any) -> None:
        self.original = original
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, connection, frame):
        self.entered.set()
        await self.release.wait()
        return await self.original(connection, frame)


async def seed(h):
    journal = Journal(h.root, h.actor)
    summary = await journal.create(SessionInit(), str(uuid4()))
    values, invocations = pending_values(summary, h.actor)
    accepted, frame = await append(journal, summary.id, values)
    return summary.id, accepted, frame, invocations


async def test_owner_metadata_and_snapshot_are_one_committed_frontier(database):
    h = database
    sid, entries, frame, _ = await seed(h)
    reads = Reads(h.root, h.actor)
    view = await reads.get(sid)
    snapshot = await reads.snapshot(sid)
    assert snapshot.view == view
    assert snapshot.view.summary == frame.projection.summary
    assert snapshot.through_index == len(entries) - 1
    assert snapshot.seq == await reads.watermark() == 2
    assert snapshot.view.pending is not None
    assert snapshot.view.pending.arguments is None
    foreign = Reads(h.root, Actor(id="different-owner"))
    assert await foreign.get(sid) is None
    assert await foreign.watermark() == 0
    with pytest.raises(UnknownSession):
        await foreign.snapshot(sid)
    with pytest.raises(UnknownSession):
        await foreign.entries(sid, start=0, limit=1)
    with pytest.raises(UnknownSession):
        await foreign.lookup(sid, kind="turn", value="turn")


async def test_history_link_group_is_scoped_and_indexed_without_payload_scan(database):
    h = database
    sid, _, _, _ = await seed(h)
    original = HistoryRef(
        provider="fabricated",
        key="old",
        sha256="a" * 64,
        owner=h.actor.id,
        session_id=sid,
        kind="message",
    )
    replacement = original.model_copy(update={"key": "new"})
    link = HistoryLink(
        message_index=0,
        previous=original,
        verified=replacement,
        canonical_digest=original.sha256,
    )
    accepted, _ = await append(
        Journal(h.root, h.actor),
        sid,
        (NewEntry(kind="history_linked", facts=link.model_dump(mode="json")),),
    )
    assert await Reads(h.root, h.actor).lookup(sid, kind="history_link", value="0") == accepted
    assert not await Reads(h.root, h.actor).lookup(sid, kind="history_link", value="1")
    with pytest.raises(UnknownSession):
        await Reads(h.root, Actor(id="other")).lookup(sid, kind="history_link", value="0")


@pytest.mark.parametrize("reverse", [False, True])
async def test_dense_keyset_entry_pages_cover_exact_original_facts(database, reverse):
    h = database
    sid, entries, _, _ = await seed(h)
    reads = Reads(h.root, h.actor)
    collected = []
    start = 1000 if reverse else 0
    while True:
        page = await reads.entries(sid, start=start, limit=3, reverse=reverse)
        collected.extend(page.items)
        if page.next_start is None:
            break
        start = page.next_start
    assert collected == list(reversed(entries) if reverse else entries)
    assert not (await reads.entries(sid, start=1000, limit=3)).items


async def test_lookup_uses_scoped_index_and_checks_count_before_byte_overflow(database):
    h = database
    sid, entries, _, invocations = await seed(h)
    reads = Reads(h.root, h.actor)
    assert await reads.lookup(sid, kind="key", value="proposal") == (entries[1],)
    assert await reads.lookup(sid, kind="key", value="absent") == ()
    indexed = await reads.lookup(sid, kind="invocation", value=invocations[0])
    assert tuple(e.index for e in indexed) == (1, 2, 4, 5, 6, 8)
    assert await reads.lookup(sid, kind="turn", value="turn") == tuple(
        e for e in entries if e.turn_id == "turn"
    )
    with pytest.raises(StoreError, match="lookup_limit"):
        await reads.lookup(sid, kind="invocation", value=invocations[0], limit=1, max_bytes=1)
    with pytest.raises(StoreError, match="lookup_bytes"):
        await reads.lookup(sid, kind="key", value="proposal", max_bytes=1)


async def test_entry_lookahead_does_not_hydrate_the_next_large_envelope(database):
    h = database
    journal = Journal(h.root, h.actor)
    summary = await journal.create(SessionInit(), str(uuid4()))
    entries, _ = await append(
        journal,
        summary.id,
        [
            NewEntry(kind="message", key="small"),
            NewEntry(kind="message", key="large", facts={"padding": "x" * 12001}),
        ],
    )
    root = None
    try:
        root = await DatabaseRoot.open(
            h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(max_batch_bytes=12000)
        )
        reads = Reads(root, h.actor)
        page = await reads.entries(summary.id, start=0, limit=1)
        assert page.items == (entries[0],)
        assert page.next_start == 1
        with pytest.raises(StoreError, match="entry_read_bytes"):
            await reads.entries(summary.id, start=1, limit=1)
        with pytest.raises(StoreError, match="lookup_bytes"):
            await reads.lookup(summary.id, kind="key", value="large", max_bytes=999999)
        with pytest.raises(StoreError, match="lookup_bytes"):
            await reads.lookup(
                summary.id,
                kind="key",
                value="small",
                max_bytes=len(canonical(entries[0].model_dump(mode="json"))) - 1,
            )
    finally:
        if root is not None:
            await retain_cleanup(close_root_and_drain(root), primary=sys.exception())


@pytest.mark.parametrize("kind", ["turn", "key"])
async def test_corrupted_lookup_membership_refuses_before_return(database, kind):
    h = database
    sid, _, _, _ = await seed(h)
    async with h.pool.connection() as connection:
        if kind == "turn":
            await connection.execute(
                sql.SQL("INSERT INTO {}.entry_groups VALUES (%s,%s,'turn',%s,0)").format(
                    sql.Identifier(h.schema)
                ),
                (h.actor.id.encode(), sid.encode(), b"forged"),
            )
        else:
            await connection.execute(
                sql.SQL(
                    "UPDATE {}.entries SET entry_key=%s WHERE owner=%s AND session=%s "
                    "AND position=0"
                ).format(sql.Identifier(h.schema)),
                (b"forged", h.actor.id.encode(), sid.encode()),
            )
    with pytest.raises(IntegrityError, match="lookup_integrity"):
        await Reads(h.root, h.actor).lookup(sid, kind=kind, value="forged")


async def test_missing_dense_entry_refuses_and_unknown_owner_stays_indistinguishable(database):
    h = database
    journal = Journal(h.root, h.actor)
    summary = await journal.create(SessionInit(), str(uuid4()))
    await append(journal, summary.id, [NewEntry(kind="message"), NewEntry(kind="message")])
    async with h.pool.connection() as connection:
        await connection.execute(
            sql.SQL("DELETE FROM {}.entries WHERE owner=%s AND session=%s AND position=0").format(
                sql.Identifier(h.schema)
            ),
            (journal.owner, summary.id.encode()),
        )
    with pytest.raises(IntegrityError, match="entry_order_invalid"):
        await Reads(h.root, h.actor).entries(summary.id, start=0, limit=5)
    foreign = Reads(h.root, Actor(id="different"))
    assert await foreign.get(summary.id) == await foreign.get("absent") is None


@pytest.mark.parametrize("operation", ["entries", "lookup"])
async def test_invalid_read_limits_refuse_without_acquiring_connection(database, operation):
    h = database
    reads = Reads(h.root, h.actor)
    before = len(h.calls)
    request = (
        reads.entries("absent", start=0, limit=0)
        if operation == "entries"
        else reads.lookup("absent", kind="turn", value="turn", limit=0)
    )
    with pytest.raises(ValueError, match="invalid_page"):
        await request
    assert len(h.calls) == before


async def blocked(pool, writer_pid, reader_pid, reading, writing):
    while True:
        if reading.done():
            reading.result()
            raise AssertionError("snapshot_reader_ended_before_blocker_witness")
        if writing.done():
            writing.result()
            raise AssertionError("snapshot_writer_ended_before_blocker_witness")
        async with pool.connection() as connection:
            cursor = await connection.execute("SELECT pg_blocking_pids(%s)", (writer_pid,))
            row = await cursor.fetchone()
        assert row is not None
        if reader_pid in row[0]:
            return


async def test_snapshot_holds_owner_watermark_until_its_pending_read_finishes(
    database, monkeypatch
):
    h = database
    sid, entries, _, _ = await seed(h)
    root = None
    reading = None
    writing = None
    source = SnapshotSource(h.pool)
    view = None

    try:
        root = await DatabaseRoot.open(source, actor=h.actor, schema=h.schema)
        reads = Reads(root, h.actor)
        view = HeldSnapshotView(reads.view)
        monkeypatch.setattr(reads, "view", view)
        reading = asyncio.create_task(reads.snapshot(sid))
        await wait_event_or_task(view.entered, reading)
        await wait_event_or_task(source.reader_entered, reading)
        assert source.reader_pid is not None
        writing = asyncio.create_task(
            append(Journal(root, h.actor), sid, [NewEntry(kind="message")])
        )
        await wait_event_or_task(source.writer_entered, writing)
        assert source.writer_pid is not None
        await blocked(h.pool, source.writer_pid, source.reader_pid, reading, writing)
        assert not writing.done()
        view.release.set()
        snapshot = await reading
        await writing
        assert snapshot.seq == 2
        assert snapshot.through_index == len(entries) - 1
        following = await reads.snapshot(sid)
        assert following.seq == 3
        assert following.through_index == len(entries)
    finally:
        if view is not None:
            view.release.set()
        if root is not None:
            await retain_cleanup(
                close_root_settle_and_finalize(root, (reading, writing)),
                primary=sys.exception(),
            )


@pytest.mark.parametrize("corruption", ["negative", "naive_time"])
@pytest.mark.parametrize("operation", ["lookup", "append"])
async def test_canonical_but_invalid_entry_frontier_refuses_without_mutation(
    database, corruption, operation
):
    h = database
    journal = Journal(h.root, h.actor)
    summary = await journal.create(SessionInit(), str(uuid4()))
    value = NewEntry(kind="message", key="original")
    accepted, _ = await append(journal, summary.id, [value])
    original = accepted[0]
    changed = original.model_copy(
        update=(
            {"index": -1} if corruption == "negative" else {"at": original.at.replace(tzinfo=None)}
        )
    )
    async with h.pool.connection() as connection:
        await connection.execute(
            sql.SQL(
                "UPDATE {}.entries SET position=%s,envelope=%s WHERE owner=%s AND session=%s"
            ).format(sql.Identifier(h.schema)),
            (
                changed.index,
                canonical(changed.model_dump(mode="json")),
                journal.owner,
                summary.id.encode(),
            ),
        )
    before = await contents(journal, summary.id)
    request = (
        Reads(h.root, h.actor).lookup(summary.id, kind="key", value="original")
        if operation == "lookup"
        else append(journal, summary.id, [NewEntry(kind="message", key="rolled-back"), value])
    )
    with pytest.raises(IntegrityError, match="entry_integrity"):
        await request
    assert await contents(journal, summary.id) == before
