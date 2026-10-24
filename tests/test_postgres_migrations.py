"""Explicit revision upgrades preserve immutable content and refuse structural drift."""

from uuid import uuid4

import pytest
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from psycopg import sql

from tether.stores.postgres import DatabaseRoot, PostgresHistory, migrate
from tether.types import Message
from tether.types.canonical import canonical, digest
from tether.types.execution import HistoryRef
from tether.types.history import HistoryRecord, StoredHistory
from tether.types.storage import IntegrityError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_revision1_upgrade_preserves_namespace_and_original_history(database):
    h = database
    schema = "prior_" + uuid4().hex
    identity = await migrate(h.source, actor=h.actor, schema=schema, revision=1)
    record = HistoryRecord(
        kind="message", message=Message(role="user", content="Fabricated prior content")
    )
    ref = HistoryRef(
        provider="postgres",
        owner=h.actor.id,
        session_id="session",
        key="key",
        codec="tether-history-v1",
        sha256=digest(record.model_dump(mode="json")),
    )
    data = canonical(StoredHistory(ref=ref, record=record).model_dump(mode="json"))
    async with h.pool.connection() as connection:
        await connection.execute(
            sql.SQL("INSERT INTO {}.history VALUES (%s,%s,%s,%s,%s)").format(
                sql.Identifier(schema)
            ),
            (h.actor.id.encode(), b"session", b"key", data, ref.sha256),
        )
    try:
        with pytest.raises(IntegrityError, match="database_revision_mismatch"):
            await DatabaseRoot.open(h.source, actor=h.actor, schema=schema)
        assert await migrate(h.source, actor=h.actor, schema=schema) == identity
        assert await migrate(h.source, actor=h.actor, schema=schema) == identity
        root = await DatabaseRoot.open(h.source, actor=h.actor, schema=schema)
        try:
            value = (await PostgresHistory(root).read([ref], actor=h.actor))[0]
            assert value.status == "ready"
            assert value.record == record
            async with h.pool.connection() as connection:
                cursor = await connection.execute(
                    sql.SQL("SELECT envelope FROM {}.history").format(sql.Identifier(schema))
                )
                assert await cursor.fetchone() == (data,)
        finally:
            assert await root.aclose()
    finally:
        async with h.pool.connection() as connection:
            await connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


@pytest.mark.parametrize("drift", ["ref_unique", "entry_unique", "missing_table", "unlogged"])
async def test_execution_schema_drift_refuses_without_repair(database, drift):
    h = database
    statements = {
        "ref_unique": sql.SQL("ALTER TABLE {}.sessions DROP CONSTRAINT sessions_owner_ref_key"),
        "entry_unique": sql.SQL(
            "ALTER TABLE {}.entries DROP CONSTRAINT entries_owner_session_entry_key_key"
        ),
        "missing_table": sql.SQL("DROP TABLE {}.pending"),
        "unlogged": sql.SQL("ALTER TABLE {}.changes SET UNLOGGED"),
    }
    async with h.pool.connection() as connection:
        await connection.execute(statements[drift].format(sql.Identifier(h.schema)))
    with pytest.raises(IntegrityError, match="database_schema_mismatch"):
        await migrate(h.source, actor=h.actor, schema=h.schema)
    with pytest.raises(IntegrityError, match="database_schema_mismatch"):
        await DatabaseRoot.open(h.source, actor=h.actor, schema=h.schema)


async def test_unmanaged_execution_table_is_not_adopted_or_overwritten(database):
    h = database
    schema = "unmanaged_" + uuid4().hex
    async with h.pool.connection() as connection:
        await connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        await connection.execute(
            sql.SQL("CREATE TABLE {}.sessions (opaque bytea)").format(sql.Identifier(schema))
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
