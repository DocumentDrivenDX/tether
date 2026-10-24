"""Real PostgreSQL18 qualification of explicit acquisition, migration and immutable history."""

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from psycopg import sql

from tether.stores.postgres import DatabaseRoot, PostgresHistory, migrate, migration_sql
from tether.types import Message
from tether.types.execution import Actor, CommitUnknown, EntryConflict, ExecutionDenied
from tether.types.history import HistoryRecord
from tether.types.storage import IntegrityError, StorageLimits, StoreError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def record(text="Fabricated content"):
    return HistoryRecord(kind="message", message=Message(role="user", content=text))


async def test_migrations_are_explicit_repeatable_serialized_and_exportable(database):
    h = database
    identities = await asyncio.gather(
        *[migrate(h.source, actor=h.actor, schema=h.schema) for _ in range(4)]
    )
    assert len(set(identities)) == 1
    assert all(isinstance(statement, str) for statement in migration_sql(h.schema))
    missing = "absent_" + uuid4().hex
    with pytest.raises(IntegrityError, match="database_schema_mismatch"):
        await DatabaseRoot.open(h.source, actor=h.actor, schema=missing)
    with pytest.raises(ValueError, match="invalid_database_schema"):
        migration_sql("untrusted; schema")


async def test_migration_revision_conflicts_refuse_without_repair(database):
    h = database
    async with h.pool.connection() as connection:
        await connection.execute(
            sql.SQL("UPDATE {}.metadata SET revision=900").format(sql.Identifier(h.schema))
        )
    with pytest.raises(IntegrityError, match="database_revision_mismatch"):
        await migrate(h.source, actor=h.actor, schema=h.schema)
    with pytest.raises(IntegrityError, match="database_revision_mismatch"):
        await DatabaseRoot.open(h.source, actor=h.actor, schema=h.schema)


async def test_equal_concurrent_writes_are_idempotent_and_conflicts_refuse(database):
    h = database
    refs = await asyncio.gather(
        *[
            h.history.put(actor=h.actor, session_id="session", key="key", record=record())
            for _ in range(4)
        ]
    )
    assert len(set(ref.sha256 for ref in refs)) == 1
    with pytest.raises(EntryConflict, match="history_conflict"):
        await h.history.put(actor=h.actor, session_id="session", key="key", record=record("Other"))
    values = await h.history.read(refs, actor=h.actor)
    assert all(value.record == record() for value in values)
    assert all(value.status == "ready" for value in values)


async def test_canonical_bytes_preserve_nul_unicode_large_numbers_and_addresses(database):
    h = database
    actor = Actor(id="owner" + chr(0))
    value = HistoryRecord(
        kind="model_exchange",
        request={"text": "snowman ☃" + chr(0), "large": 10**1000},
        response={"value": 1.7976931348623157e308},
    )
    ref = await h.history.put(
        actor=actor, session_id="session" + chr(0), key="key" + chr(0), record=value
    )
    read = (await h.history.read([ref], actor=actor))[0]
    assert read.status == "ready"
    assert read.record == value


async def test_owner_codec_scope_and_digests_are_verified(database):
    h = database
    ref = await h.history.put(actor=h.actor, session_id="session", key="key", record=record())
    before = len(h.calls)
    denied = await h.history.read([ref], actor=Actor(id="foreign"))
    unsupported = await h.history.read([ref.model_copy(update={"codec": "unknown"})], actor=h.actor)
    assert denied[0].status == "denied"
    assert unsupported[0].status == "unsupported"
    assert len(h.calls) == before
    refs = [ref.model_copy(update={"key": "missing"}), ref.model_copy(update={"sha256": "wrong"})]
    values = await h.history.read(refs, actor=h.actor)
    assert [v.status for v in values] == ["missing", "integrity_error"]
    async with h.pool.connection() as connection:
        await connection.execute(
            sql.SQL("UPDATE {}.history SET envelope=%s").format(sql.Identifier(h.schema)),
            (b"malformed",),
        )
    assert (await h.history.read([ref], actor=h.actor))[0].status == "integrity_error"


async def test_reads_and_writes_have_finite_byte_and_count_limits(database):
    h = database
    ref = await h.history.put(
        actor=h.actor, session_id="session", key="key", record=record("x" * 500)
    )
    small = await DatabaseRoot.open(
        h.source,
        actor=h.actor,
        schema=h.schema,
        limits=StorageLimits(max_batch_bytes=400, page_limit=1),
    )
    history = PostgresHistory(small)
    try:
        values = await history.read([ref], actor=h.actor)
        assert values[0].status == "unavailable"
        assert values[0].record is None
        with pytest.raises(StoreError, match="history_read_limit"):
            await history.read([ref, ref], actor=h.actor)
        with pytest.raises(StoreError, match="history_write_bytes"):
            await history.put(
                actor=h.actor, session_id="session", key="other", record=record("x" * 500)
            )
    finally:
        assert await small.aclose()


async def test_acquisition_actor_is_fresh_and_failure_never_falls_back(database):
    h = database
    actor = h.actor.model_copy(deep=True)
    actor.attributes["scope"] = "original"
    seen = []

    @asynccontextmanager
    async def source(caller, session_id):
        seen.append((caller, session_id))
        caller.attributes["scope"] = "mutated"
        if len(seen) > 1:
            raise ExecutionDenied("caller_acquisition_denied")
        async with h.pool.connection() as connection:
            yield connection

    root = await DatabaseRoot.open(source, actor=actor, schema=h.schema)
    try:
        with pytest.raises(ExecutionDenied, match="caller_acquisition_denied"):
            await PostgresHistory(root).put(
                actor=actor, session_id="session", key="key", record=record()
            )
        assert actor.attributes == {"scope": "original"}
        assert seen[-1][1] == "session"
        assert len(seen) == 2
    finally:
        assert await root.aclose()


async def test_ambient_transaction_is_not_an_acknowledged_savepoint(database):
    h = database

    @asynccontextmanager
    async def source(actor, session_id):
        async with h.pool.connection() as connection:
            await connection.execute("SELECT 1")
            yield connection

    with pytest.raises(IntegrityError, match="ambient_database_transaction"):
        await DatabaseRoot.open(source, actor=h.actor, schema=h.schema)


async def test_release_failure_after_commit_is_unknown_and_retry_finds_content(database):
    h = database
    fail = False

    @asynccontextmanager
    async def source(actor, session_id):
        async with h.pool.connection() as connection:
            yield connection
        if fail:
            raise RuntimeError("fabricated_release_failure")

    root = await DatabaseRoot.open(source, actor=h.actor, schema=h.schema)
    history = PostgresHistory(root)
    try:
        fail = True
        with pytest.raises(CommitUnknown, match="database_release_unknown"):
            await history.put(actor=h.actor, session_id="session", key="key", record=record())
        fail = False
        ref = await history.put(actor=h.actor, session_id="session", key="key", record=record())
        assert (await h.history.read([ref], actor=h.actor))[0].record == record()
    finally:
        assert await root.aclose()


async def test_shared_namespace_survives_root_close_and_outstanding_workers(database):
    h = database
    sibling = await DatabaseRoot.open(h.source, actor=h.actor, schema=h.schema)
    assert sibling.workers is h.root.workers
    pending = asyncio.get_running_loop().create_future()
    h.root.workers.retain("session", pending)
    assert not await h.root.aclose()
    assert not sibling.workers.closed
    assert await sibling.run(h.actor, None, lambda connection: connection.execute("SELECT 1"))
    assert not await sibling.aclose()
    replacement = await DatabaseRoot.open(h.source, actor=h.actor, schema=h.schema)
    assert replacement.workers is h.root.workers
    assert replacement.workers.busy("session")
    pending.set_result(None)
    await asyncio.sleep(0)
    assert await replacement.aclose()


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (ExecutionDenied("denied"), "denied"),
        (StoreError("down"), "unavailable"),
        (RuntimeError("callback_failure"), "unavailable"),
    ],
)
async def test_direct_history_reads_preserve_ordered_failure_slots(database, failure, status):
    h = database
    ref = await h.history.put(actor=h.actor, session_id="session", key="key", record=record())
    deny = False

    @asynccontextmanager
    async def source(actor, session_id):
        if deny:
            raise failure
        async with h.pool.connection() as connection:
            yield connection

    root = await DatabaseRoot.open(source, actor=h.actor, schema=h.schema)
    try:
        deny = True
        refs = [ref, ref.model_copy(update={"key": "other"})]
        values = await PostgresHistory(root).read(refs, actor=h.actor)
        assert [value.ref for value in values] == refs
        assert [value.status for value in values] == [status, status]
        assert all(value.record is None for value in values)
    finally:
        assert await root.aclose()


async def test_migration_refuses_unmanaged_tables_without_stamping_metadata(database):
    h = database
    schema = "unmanaged_" + uuid4().hex
    async with h.pool.connection() as connection:
        await connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        await connection.execute(
            sql.SQL(
                "CREATE TABLE {}.history (owner bytea, session bytea, "
                "key bytea, envelope bytea, sha256 text)"
            ).format(sql.Identifier(schema))
        )
    try:
        with pytest.raises(IntegrityError, match="unmanaged_database_schema"):
            await migrate(h.source, actor=h.actor, schema=schema)
        async with h.pool.connection() as connection:
            cursor = await connection.execute("SELECT to_regclass(%s)", (schema + ".metadata",))
            assert await cursor.fetchone() == (None,)
    finally:
        async with h.pool.connection() as connection:
            await connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


@pytest.mark.parametrize(
    "drift",
    ["missing_table", "missing_primary", "changed_type", "unlogged_history", "unlogged_metadata"],
)
async def test_managed_schema_drift_refuses_without_implicit_repair(database, drift):
    h = database
    operations = {
        "missing_table": sql.SQL("DROP TABLE {}.history"),
        "unlogged_history": sql.SQL("ALTER TABLE {}.history SET UNLOGGED"),
        "unlogged_metadata": sql.SQL("ALTER TABLE {}.metadata SET UNLOGGED"),
        "missing_primary": sql.SQL("ALTER TABLE {}.history DROP CONSTRAINT history_pkey"),
        "changed_type": sql.SQL(
            "ALTER TABLE {}.history ALTER COLUMN sha256 TYPE bytea USING sha256::bytea"
        ),
    }
    async with h.pool.connection() as connection:
        await connection.execute(operations[drift].format(sql.Identifier(h.schema)))
    with pytest.raises(IntegrityError, match="database_schema_mismatch"):
        await migrate(h.source, actor=h.actor, schema=h.schema)
    with pytest.raises(IntegrityError, match="database_schema_mismatch"):
        await DatabaseRoot.open(h.source, actor=h.actor, schema=h.schema)


async def test_malformed_envelopes_still_consume_the_aggregate_read_budget(database):
    h = database
    refs = [
        await h.history.put(actor=h.actor, session_id="session", key=key, record=record())
        for key in ["first", "second"]
    ]
    async with h.pool.connection() as connection:
        await connection.execute(
            sql.SQL("UPDATE {}.history SET envelope=%s").format(sql.Identifier(h.schema)),
            (b"x" * 3000,),
        )
    root = await DatabaseRoot.open(
        h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(max_batch_bytes=4096)
    )
    try:
        values = await PostgresHistory(root).read(refs, actor=h.actor)
        assert [value.status for value in values] == ["integrity_error", "unavailable"]
        assert all(value.record is None for value in values)
    finally:
        assert await root.aclose()


async def test_closed_acquired_connection_is_unavailable_without_payload(database):
    h = database
    ref = await h.history.put(actor=h.actor, session_id="session", key="key", record=record())
    closed = False

    @asynccontextmanager
    async def source(actor, session_id):
        async with h.pool.connection() as connection:
            if closed:
                await connection.close()
            yield connection

    root = await DatabaseRoot.open(source, actor=h.actor, schema=h.schema)
    try:
        closed = True
        values = await PostgresHistory(root).read([ref], actor=h.actor)
        assert values[0].status == "unavailable"
        assert values[0].record is None
    finally:
        assert await root.aclose()
