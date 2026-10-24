"""Persisted polling and unresolved-effect pages retain scoped original facts."""

import asyncio
import json
from uuid import uuid4

import pytest
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from psycopg import sql
from test_postgres_primitives import append

from tether.stores.postgres import DatabaseRoot
from tether.stores.postgres.changes import Changes
from tether.stores.postgres.journal import Journal
from tether.types.canonical import canonical
from tether.types.execution import Actor, NewEntry
from tether.types.storage import (
    ChangeGap,
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


async def test_filtered_feed_advances_over_unrelated_owner_changes(database):
    h = database
    root = await DatabaseRoot.open(
        h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(page_limit=1)
    )
    journal = Journal(root, h.actor)
    try:
        first = await journal.create(SessionInit(), str(uuid4()))
        second = await journal.create(SessionInit(), str(uuid4()))
        await append(journal, second.id, [NewEntry(kind="message")])
        accepted, _ = await append(journal, first.id, [NewEntry(kind="message")])
        changes = Changes(root, h.actor)
        feed = changes.changes(session_id=first.id, since=1)
        try:
            value = await asyncio.wait_for(anext(feed), 3)
            assert value.seq == 4
            assert value.session_id == first.id
            assert value.entries == accepted
        finally:
            await feed.aclose()
        foreign = Changes(root, Actor(id="different"))
        assert await foreign.poll(0) == ((), 0)
        feed = foreign.changes(session_id=first.id, since=0)
        with pytest.raises(UnknownSession):
            await anext(feed)
    finally:
        assert await root.aclose()


async def test_retention_floor_is_explicit_and_cannot_move_backwards(database):
    h = database
    root = await DatabaseRoot.open(
        h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(feed_retention=2)
    )
    journal = Journal(root, h.actor)
    try:
        summary = await journal.create(SessionInit(), str(uuid4()))
        for _ in range(3):
            await append(journal, summary.id, [NewEntry(kind="message")])
        changes = Changes(root, h.actor)
        with pytest.raises(ChangeGap, match="too_old"):
            await changes.poll(1)
        values, position = await changes.poll(2)
        assert [v.seq for v in values] == [3, 4]
        assert position == 4
        # The original larger-retention root must not lower an already published floor.
        await append(Journal(h.root, h.actor), summary.id, [NewEntry(kind="message")])
        with pytest.raises(ChangeGap, match="too_old"):
            await changes.poll(1)
        feed = changes.changes(since=6)
        with pytest.raises(ValueError, match="invalid_sequence"):
            await anext(feed)
    finally:
        assert await root.aclose()


async def test_feed_yields_bounded_prefixes_without_skipping_an_oversized_change(database):
    h = database
    journal = Journal(h.root, h.actor)
    summary = await journal.create(SessionInit(), str(uuid4()))
    for _ in range(4):
        await append(journal, summary.id, [NewEntry(kind="message", facts={"padding": "x" * 7000})])
    root = await DatabaseRoot.open(
        h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(max_batch_bytes=12000)
    )
    try:
        changes = Changes(root, h.actor)
        position, seen = 0, []
        while position < 5:
            values, position = await changes.poll(position)
            assert values
            assert len(values) < 5
            seen.extend(v.seq for v in values)
        assert seen == [1, 2, 3, 4, 5]
        assert await changes.poll(position) == ((), 5)
        await append(
            journal, summary.id, [NewEntry(kind="message", facts={"padding": "x" * 12001})]
        )
        with pytest.raises(StoreError, match="change_read_bytes"):
            await changes.poll(position)
        assert await changes.watermark() == 6
    finally:
        assert await root.aclose()


@pytest.mark.parametrize(
    "corruption", ["hole", "whitespace", "owner", "sequence", "entry_time", "entry_index"]
)
async def test_feed_refuses_corrupted_retained_facts(database, corruption):
    h = database
    journal = Journal(h.root, h.actor)
    summary = await journal.create(SessionInit(), str(uuid4()))
    await append(journal, summary.id, [NewEntry(kind="message")])
    async with h.pool.connection() as connection:
        cursor = await connection.execute(
            sql.SQL("SELECT envelope FROM {}.changes WHERE owner=%s AND seq=2").format(
                sql.Identifier(h.schema)
            ),
            (journal.owner,),
        )
        raw = (await cursor.fetchone())[0]
        if corruption == "hole":
            await connection.execute(
                sql.SQL("DELETE FROM {}.changes WHERE owner=%s AND seq=2").format(
                    sql.Identifier(h.schema)
                ),
                (journal.owner,),
            )
        else:
            payload = json.loads(raw)
            if corruption == "owner":
                payload["summary"]["owner"] = "different"
            elif corruption == "sequence":
                payload["summary"]["last_seq"] = 999
            elif corruption == "entry_time":
                payload["entries"][0]["at"] = "2026-01-01T00:00:00"
            elif corruption == "entry_index":
                payload["entries"][0]["index"] = -1
            changed = b" " + raw if corruption == "whitespace" else canonical(payload)
            await connection.execute(
                sql.SQL("UPDATE {}.changes SET envelope=%s WHERE owner=%s AND seq=2").format(
                    sql.Identifier(h.schema)
                ),
                (changed, journal.owner),
            )
    with pytest.raises(IntegrityError):
        await Changes(h.root, h.actor).poll(0)


async def test_oversized_cached_feed_identity_is_not_transferred(database):
    h = database
    journal = Journal(h.root, h.actor)
    summary = await journal.create(SessionInit(), str(uuid4()))
    await append(journal, summary.id, [NewEntry(kind="message")])
    async with h.pool.connection() as connection:
        await connection.execute(
            sql.SQL("UPDATE {}.changes SET session=%s WHERE owner=%s AND seq=2").format(
                sql.Identifier(h.schema)
            ),
            (b"x" * 12001, journal.owner),
        )
    root = await DatabaseRoot.open(
        h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(max_batch_bytes=12000)
    )
    try:
        changes = Changes(root, h.actor)
        values, position = await changes.poll(0)
        assert [v.seq for v in values] == [1]
        assert position == 1
        with pytest.raises(StoreError, match="change_read_bytes"):
            await changes.poll(position)
    finally:
        assert await root.aclose()


async def test_effect_pages_preserve_intent_positions_and_verify_index_membership(database):
    h = database
    journal = Journal(h.root, h.actor)
    summary = await journal.create(SessionInit(), str(uuid4()))
    await append(
        journal,
        summary.id,
        [
            NewEntry(
                kind="tool_call",
                facts={
                    "invocation_id": f"inv-{i}",
                    "tool": "fabricated",
                    "effect": "effectful",
                    "status": "started",
                },
            )
            for i in range(3)
        ],
    )
    reads = Changes(h.root, h.actor)
    first = await reads.effects(summary.id, limit=2)
    assert [v.intent_index for v in first.items] == [0, 1]
    assert first.next_start == 2
    assert [v.invocation_id for v in (await reads.effects(summary.id, start=2)).items] == ["inv-2"]
    await append(
        journal,
        summary.id,
        [
            NewEntry(
                kind="resolved",
                facts={
                    "invocation_id": "inv-0",
                    "outcome": "not_completed",
                },
            )
        ],
    )
    assert [v.intent_index for v in (await reads.effects(summary.id)).items] == [1, 2]
    async with h.pool.connection() as connection:
        await connection.execute(
            sql.SQL(
                "UPDATE {}.effects SET invocation=%s WHERE owner=%s AND session=%s AND position=1"
            ).format(sql.Identifier(h.schema)),
            (b"x" * 12001, journal.owner, summary.id.encode()),
        )
    with pytest.raises(IntegrityError, match="effect_integrity"):
        await reads.effects(summary.id)


async def test_filtered_feed_skips_unrelated_oversized_content_with_scoped_acquisition(database):
    h = database
    journal = Journal(h.root, h.actor)
    selected = await journal.create(SessionInit(), str(uuid4()))
    unrelated = await journal.create(SessionInit(), str(uuid4()))
    await append(journal, unrelated.id, [NewEntry(kind="message", facts={"padding": "x" * 12001})])
    accepted, _ = await append(journal, selected.id, [NewEntry(kind="message")])
    root = await DatabaseRoot.open(
        h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(max_batch_bytes=12000)
    )
    try:
        reads = Changes(root, h.actor)
        before = len(h.calls)
        feed = reads.changes(session_id=selected.id, since=1)
        try:
            change = await asyncio.wait_for(anext(feed), 3)
            assert change.seq == 4
            assert change.entries == accepted
            assert all(session == selected.id for _, session in h.calls[before:])
        finally:
            await feed.aclose()
        with pytest.raises(StoreError, match="change_read_bytes"):
            await reads.poll(2, session_id=unrelated.id)
        async with h.pool.connection() as connection:
            await connection.execute(
                sql.SQL("UPDATE {}.changes SET session=%s WHERE owner=%s AND seq=3").format(
                    sql.Identifier(h.schema)
                ),
                (b"x" * 12001, journal.owner),
            )
        values, position = await reads.poll(1, session_id=selected.id)
        assert [v.seq for v in values] == [4]
        assert position == 4
    finally:
        assert await root.aclose()


async def test_duplicate_effect_position_in_lookahead_cannot_repeat_the_page(database):
    h = database
    journal = Journal(h.root, h.actor)
    summary = await journal.create(SessionInit(), str(uuid4()))
    await append(
        journal,
        summary.id,
        [
            NewEntry(
                kind="tool_call",
                facts={
                    "invocation_id": "original",
                    "tool": "fabricated",
                    "effect": "effectful",
                    "status": "started",
                },
            )
        ],
    )
    async with h.pool.connection() as connection:
        await connection.execute(
            sql.SQL("INSERT INTO {}.effects VALUES (%s,%s,%s,0)").format(sql.Identifier(h.schema)),
            (journal.owner, summary.id.encode(), b"forged"),
        )
    with pytest.raises(IntegrityError, match="effect_integrity"):
        await Changes(h.root, h.actor).effects(summary.id, start=0, limit=1)
