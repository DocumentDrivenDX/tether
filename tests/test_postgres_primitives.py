"""Internal SQL transactions are compared to journal reconstruction before public exposure."""

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from psycopg import sql

from tether.stores.postgres import DatabaseRoot
from tether.stores.postgres.journal import Journal
from tether.stores.projections import create_index
from tether.types import TokenUsage
from tether.types.canonical import canonical
from tether.types.execution import Actor, CommitUnknown, Entry, EntryConflict, NewEntry
from tether.types.storage import (
    IntegrityError,
    PersistedChange,
    SessionInit,
    StorageLimits,
    StoreError,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def append(journal, session_id, values):
    async def write(connection):
        await journal.head(connection, write=True)
        frame = await journal.frame(connection, session_id, write=True)
        accepted, inserted = await journal.append(
            connection, frame, values, await journal.clock(connection)
        )
        if inserted:
            await journal.publish(connection, frame, inserted)
        return accepted, frame

    return await journal.root.run(journal.actor, session_id, write, write=True)


async def contents(journal, session_id):
    async def read(connection):
        head = await journal.head(connection, write=False)
        frame = await journal.frame(connection, session_id, write=False)
        cursor = await connection.execute(
            sql.SQL(
                "SELECT envelope FROM {} WHERE owner=%s AND session=%s ORDER BY position"
            ).format(journal.table("entries")),
            (journal.owner, session_id.encode()),
        )
        entries = tuple(Entry.model_validate_json(row[0]) for row in await cursor.fetchall())
        return head, frame, entries

    return await journal.root.run(journal.actor, session_id, read)


async def test_concurrent_reference_create_is_one_session_sequence_and_change(database):
    h = database
    journal = Journal(h.root, h.actor)
    init = SessionInit(
        ref="ref" + chr(0),
        title="Fabricated ☃" + chr(0),
        tags=("tag" + chr(0),),
        settings={"approval": "manual", "held": {"fabricated": ["item"]}},
    )
    summaries = await asyncio.gather(*[journal.create(init, str(uuid4())) for _ in range(5)])
    assert len({summary.id for summary in summaries}) == 1
    assert all(summary.last_seq == 1 for summary in summaries)
    head, frame, entries = await contents(journal, summaries[0].id)
    assert head == (1, 0)
    assert not entries
    assert frame.projection.init == init
    assert frame.projection.state.approval == "manual"
    assert frame.projection.state.held == {"fabricated": ("item",)}


async def test_atomic_append_idempotency_conflicts_and_rebuild_projection(database):
    h = database
    journal = Journal(h.root, h.actor)
    initial = SessionInit(title="Initial")
    summary = await journal.create(initial, str(uuid4()))
    values = [
        NewEntry(
            kind="turn_started",
            turn_id="turn",
            key="start",
            facts={"spec_id": "demo", "spec_version": "1", "spec_digest": "digest"},
        ),
        NewEntry(kind="message", turn_id="turn", key="message"),
        NewEntry(kind="session", facts={"event": "titled", "value": "Changed"}),
        NewEntry(kind="turn_ended", turn_id="turn", facts={"stop_reason": "interrupted"}),
    ]
    accepted, frame = await append(journal, summary.id, values)
    oracle = create_index(summary.id, h.actor.id, initial, summary.created_at)
    for entry in accepted:
        oracle.apply(entry)
    oracle.summary = oracle.summary.model_copy(
        update={"last_seq": frame.projection.summary.last_seq}
    )
    assert frame.projection.summary == oracle.summary
    assert frame.projection.state == oracle.state
    assert frame.projection.init == oracle.init
    retry, _ = await append(journal, summary.id, values[:2])
    assert retry == accepted[:2]
    assert (await contents(journal, summary.id))[0] == (2, 0)
    with pytest.raises(EntryConflict, match="entry_conflict"):
        await append(journal, summary.id, [values[0].model_copy(update={"facts": {}})])
    assert (await contents(journal, summary.id))[2] == accepted


async def test_failed_transition_rolls_back_entries_projection_and_owner_head(database):
    h = database
    journal = Journal(h.root, h.actor)
    summary = await journal.create(SessionInit(), str(uuid4()))
    with pytest.raises(IntegrityError, match="settings_invalid"):
        await append(
            journal,
            summary.id,
            [
                NewEntry(kind="message", key="first"),
                NewEntry(kind="settings", facts={"changes": "invalid"}),
            ],
        )
    head, frame, entries = await contents(journal, summary.id)
    assert head == (1, 0)
    assert frame.projection.summary.entry_count == 0
    assert not entries


def attempt(key, usage, *, suffix="", status="ok"):
    return NewEntry(
        kind="model_attempt",
        key=key + suffix,
        facts={
            "identity": {"attempt_id": key},
            "status": status,
            "usage": usage.model_dump(mode="json") if usage is not None else None,
        },
    )


async def test_usage_subcounts_missing_and_duplicate_attempts_match_rebuild(database):
    h = database
    journal = Journal(h.root, h.actor)
    initial = SessionInit()
    summary = await journal.create(initial, str(uuid4()))
    first = TokenUsage(input_tokens=7, output_tokens=3, cache_read_tokens=0, reasoning_tokens=2)
    second = TokenUsage(input_tokens=11, output_tokens=5, cache_read_tokens=4, cache_write_tokens=1)
    values = [
        attempt("one", None),
        attempt("one", first, suffix="known"),
        attempt("one", None, suffix="missing"),
        attempt("one", first, suffix="repeat"),
        attempt("two", second, status="failed"),
        attempt("three", None),
    ]
    first_accepted, first_frame = await append(journal, summary.id, values[:4])
    assert first_frame.projection.summary.usage.cache_read_tokens == 0
    assert first_frame.projection.summary.usage.cache_write_tokens is None
    assert first_frame.projection.summary.usage_complete
    remaining, frame = await append(journal, summary.id, values[4:])
    accepted = first_accepted + remaining
    oracle = create_index(summary.id, h.actor.id, initial, summary.created_at)
    for entry in accepted:
        oracle.apply(entry)
    assert frame.projection.summary.usage == oracle.summary.usage
    assert frame.projection.summary.usage == TokenUsage(
        input_tokens=18,
        output_tokens=8,
        cache_read_tokens=4,
        cache_write_tokens=1,
        reasoning_tokens=2,
    )
    assert not frame.projection.summary.usage_complete
    assert frame.missing_usage == 1
    with pytest.raises(IntegrityError, match="usage_changed"):
        await append(
            journal,
            summary.id,
            [attempt("two", second.model_copy(update={"cache_read_tokens": 5}), suffix="conflict")],
        )
    assert (await contents(journal, summary.id))[2] == accepted


async def test_retention_floor_and_persisted_changes_are_in_the_same_transactions(database):
    h = database
    root = await DatabaseRoot.open(
        h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(feed_retention=2)
    )
    journal = Journal(root, h.actor)
    try:
        summary = await journal.create(SessionInit(), str(uuid4()))
        for index in range(4):
            await append(journal, summary.id, [NewEntry(kind="message", key=str(index))])
        assert (await contents(journal, summary.id))[0] == (5, 3)
        async with h.pool.connection() as connection:
            cursor = await connection.execute(
                sql.SQL("SELECT envelope FROM {} ORDER BY seq").format(journal.table("changes"))
            )
            changes = [
                PersistedChange.model_validate_json(row[0]) for row in await cursor.fetchall()
            ]
        assert [change.seq for change in changes] == [4, 5]
        assert changes[-1].summary is not None
        assert changes[-1].summary.entry_count == 4
    finally:
        assert await root.aclose()


async def test_unknown_commit_retry_observes_original_key_without_a_new_change(database):
    h = database
    fail = False

    @asynccontextmanager
    async def source(actor, session_id):
        async with h.pool.connection() as connection:
            yield connection
        if fail:
            raise RuntimeError("fabricated_ack_loss")

    root = await DatabaseRoot.open(source, actor=h.actor, schema=h.schema)
    journal = Journal(root, h.actor)
    try:
        summary = await journal.create(SessionInit(), str(uuid4()))
        values = [NewEntry(kind="message", key="original")]
        fail = True
        with pytest.raises(CommitUnknown, match="database_release_unknown"):
            await append(journal, summary.id, values)
        fail = False
        accepted, _ = await append(journal, summary.id, values)
        assert accepted[0].index == 0
        assert (await contents(journal, summary.id))[0] == (2, 0)
        assert len((await contents(journal, summary.id))[2]) == 1
    finally:
        assert await root.aclose()


async def test_projection_byte_limit_refuses_without_creating_owner_or_session(database):
    h = database
    root = await DatabaseRoot.open(
        h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(max_batch_bytes=1000)
    )
    journal = Journal(root, Actor(id="different-owner"))
    try:
        with pytest.raises(StoreError, match="projection_write_bytes"):
            await journal.create(SessionInit(settings={"large": "x" * 2000}), str(uuid4()))
        async with h.pool.connection() as connection:
            cursor = await connection.execute(
                sql.SQL("SELECT count(*) FROM {} WHERE owner=%s").format(
                    journal.table("owner_heads")
                ),
                (journal.owner,),
            )
            assert await cursor.fetchone() == (0,)
    finally:
        assert await root.aclose()


async def test_existing_key_batch_charges_every_returned_envelope(database):
    h = database
    journal = Journal(h.root, h.actor)
    summary = await journal.create(SessionInit(), str(uuid4()))
    values = [NewEntry(kind="message", key=str(i), facts={"padding": "x" * 1700}) for i in range(2)]
    for value in values:
        await append(journal, summary.id, [value])
    before = await contents(journal, summary.id)
    root = await DatabaseRoot.open(
        h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(max_batch_bytes=3000)
    )
    try:
        bounded = Journal(root, h.actor)
        for value in values:
            assert len((await append(bounded, summary.id, [value]))[0]) == 1
        with pytest.raises(StoreError, match="entry_read_bytes"):
            await append(bounded, summary.id, values)
        assert await contents(journal, summary.id) == before
    finally:
        assert await root.aclose()


async def test_oversized_existing_usage_rolls_back_entire_append(database):
    h = database
    journal = Journal(h.root, h.actor)
    summary = await journal.create(SessionInit(), str(uuid4()))
    await append(journal, summary.id, [attempt("one", TokenUsage(input_tokens=1, output_tokens=0))])
    before = await contents(journal, summary.id)
    root = await DatabaseRoot.open(
        h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(max_batch_bytes=3000)
    )
    try:
        async with h.pool.connection() as connection:
            await connection.execute(
                sql.SQL("UPDATE {} SET usage=%s WHERE owner=%s AND session=%s").format(
                    journal.table("attempt_usage")
                ),
                (b"x" * 3001, journal.owner, summary.id.encode()),
            )
        with pytest.raises(StoreError, match="usage_read_bytes"):
            await append(
                Journal(root, h.actor),
                summary.id,
                [NewEntry(kind="message", key="rolled-back"), attempt("one", None, suffix="retry")],
            )
        assert await contents(journal, summary.id) == before
        async with h.pool.connection() as connection:
            for name, expected in (("entry_groups", 0), ("attempt_usage", 1)):
                cursor = await connection.execute(
                    sql.SQL("SELECT count(*) FROM {} WHERE owner=%s AND session=%s").format(
                        journal.table(name)
                    ),
                    (journal.owner, summary.id.encode()),
                )
                assert await cursor.fetchone() == (expected,)
    finally:
        assert await root.aclose()


@pytest.mark.parametrize("encoding", ["whitespace", "omitted_defaults"])
async def test_noncanonical_existing_entry_refuses_without_mutation(database, encoding):
    h = database
    journal = Journal(h.root, h.actor)
    summary = await journal.create(SessionInit(), str(uuid4()))
    value = NewEntry(kind="message", key="original")
    accepted, _ = await append(journal, summary.id, [value])
    entry = accepted[0]
    content = canonical(entry.model_dump(mode="json"))
    corrupted = (
        b" \n" + content
        if encoding == "whitespace"
        else canonical(entry.model_dump(mode="json", exclude_defaults=True))
    )
    assert corrupted != content
    assert Entry.model_validate_json(corrupted) == entry
    async with h.pool.connection() as connection:
        await connection.execute(
            sql.SQL("UPDATE {} SET envelope=%s WHERE owner=%s AND session=%s").format(
                journal.table("entries")
            ),
            (corrupted, journal.owner, summary.id.encode()),
        )
    before = await contents(journal, summary.id)
    with pytest.raises(IntegrityError, match="entry_integrity"):
        await append(journal, summary.id, [NewEntry(kind="message", key="rolled-back"), value])
    assert await contents(journal, summary.id) == before
    async with h.pool.connection() as connection:
        cursor = await connection.execute(
            sql.SQL("SELECT envelope FROM {} WHERE owner=%s AND session=%s").format(
                journal.table("entries")
            ),
            (journal.owner, summary.id.encode()),
        )
        assert await cursor.fetchall() == [(corrupted,)]
