"""Explicit exportable migrations; ordinary root construction only verifies them."""

import hashlib
import re
from typing import LiteralString, cast
from uuid import UUID

from psycopg import AsyncConnection, sql
from psycopg.pq import TransactionStatus

from tether.types.execution import Actor, CommitUnknown, copy_actor
from tether.types.storage import IntegrityError, StorageLimits, StoreError

from .io import PendingIO
from .source import ConnectionSource

REVISION = 1
_DDL = """CREATE TABLE {schema}.history (
    owner bytea NOT NULL, session bytea NOT NULL, key bytea NOT NULL,
    envelope bytea NOT NULL, sha256 text NOT NULL,
    PRIMARY KEY (owner, session, key))"""
CHECKSUM = hashlib.sha256(_DDL.encode()).hexdigest()


def qualified(schema: str) -> str:
    if re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema) is None:
        raise ValueError("invalid_database_schema")
    return '"' + schema + '"'


def migration_sql(schema: str) -> tuple[str, ...]:
    name = qualified(schema)
    return (
        f"CREATE SCHEMA IF NOT EXISTS {name}",
        f"CREATE TABLE {name}.metadata ("
        "singleton boolean PRIMARY KEY CHECK (singleton), namespace uuid NOT NULL, "
        "revision integer NOT NULL, checksum text NOT NULL)",
        _DDL.format(schema=name),
        sql.SQL(
            "INSERT INTO {}.metadata VALUES (true, gen_random_uuid(), {}, {}) "
            "ON CONFLICT (singleton) DO NOTHING"
        )
        .format(sql.Identifier(schema), sql.Literal(REVISION), sql.Literal(CHECKSUM))
        .as_string(),
    )


def idle(connection: AsyncConnection[tuple[object, ...]]) -> None:
    if connection.closed:
        raise StoreError("database_unavailable")
    if connection.info.transaction_status != TransactionStatus.IDLE:
        raise IntegrityError("ambient_database_transaction")
    if connection.info.server_version // 10000 != 18:
        raise IntegrityError("unsupported_database_version")


async def inspect(connection: AsyncConnection[tuple[object, ...]], schema: str) -> UUID:
    settings = await connection.execute(
        "SELECT current_setting('fsync'), current_setting('full_page_writes'), "
        "current_setting('synchronous_commit')"
    )
    if await settings.fetchone() != ("on", "on", "on"):
        raise IntegrityError("unsupported_database_durability")
    await _shape(
        connection,
        schema,
        "metadata",
        (
            ("singleton", "boolean", True),
            ("namespace", "uuid", True),
            ("revision", "integer", True),
            ("checksum", "text", True),
        ),
        ["singleton"],
    )
    await _shape(
        connection,
        schema,
        "history",
        (
            ("owner", "bytea", True),
            ("session", "bytea", True),
            ("key", "bytea", True),
            ("envelope", "bytea", True),
            ("sha256", "text", True),
        ),
        ["owner", "session", "key"],
    )
    cursor = await connection.execute(
        sql.SQL("SELECT singleton, namespace, revision, checksum FROM {}.metadata").format(
            sql.Identifier(schema)
        )
    )
    rows = await cursor.fetchall()
    if len(rows) != 1:
        raise IntegrityError("database_revision_mismatch")
    row = rows[0]
    if row[0] is not True or row[2:] != (REVISION, CHECKSUM) or not isinstance(row[1], UUID):
        raise IntegrityError("database_revision_mismatch")
    return row[1]


async def _shape(
    connection: AsyncConnection[tuple[object, ...]],
    schema: str,
    table: str,
    columns: tuple[tuple[str, str, bool], ...],
    primary: list[str],
) -> None:
    address = qualified(schema) + "." + table
    relation = await connection.execute(
        "SELECT relkind, relpersistence FROM pg_catalog.pg_class WHERE oid=to_regclass(%s)",
        (address,),
    )
    if await relation.fetchone() != ("r", "p"):
        raise IntegrityError("database_schema_mismatch")
    cursor = await connection.execute(
        "SELECT attname::text, atttypid::regtype::text, attnotnull FROM pg_catalog.pg_attribute "
        "WHERE attrelid=to_regclass(%s) AND attnum>0 AND NOT attisdropped ORDER BY attnum",
        (address,),
    )
    if await cursor.fetchall() != list(columns):
        raise IntegrityError("database_schema_mismatch")
    cursor = await connection.execute(
        "SELECT array_agg(a.attname::text ORDER BY k.position) FROM pg_catalog.pg_constraint c "
        "CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(number, position) "
        "JOIN pg_catalog.pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.number "
        "WHERE c.conrelid=to_regclass(%s) AND c.contype='p'",
        (address,),
    )
    if await cursor.fetchone() != (primary,):
        raise IntegrityError("database_schema_mismatch")


async def migrate(
    source: ConnectionSource,
    *,
    actor: Actor,
    schema: str,
    limits: StorageLimits | None = None,
) -> UUID:
    statements = migration_sql(schema)
    lock = int.from_bytes(hashlib.sha256(schema.encode()).digest()[:8], signed=True)
    actor = copy_actor(actor)
    pending = PendingIO(limits or StorageLimits())

    async def apply() -> UUID:
        complete = False
        try:
            async with source(copy_actor(actor), None) as connection:
                idle(connection)
                async with connection.transaction():
                    await connection.execute("SELECT pg_advisory_xact_lock(%s)", (lock,))
                    existing = await connection.execute(
                        "SELECT to_regclass(%s), to_regclass(%s)",
                        (qualified(schema) + ".metadata", qualified(schema) + ".history"),
                    )
                    row = await existing.fetchone()
                    if row is not None and row[0] is not None:
                        identity = await inspect(connection, schema)
                    else:
                        if row is not None and row[1] is not None:
                            raise IntegrityError("unmanaged_database_schema")
                        for statement in statements:
                            await connection.execute(sql.SQL(cast(LiteralString, statement)))
                        identity = await inspect(connection, schema)
                    complete = True
            return identity
        except Exception as error:
            if complete:
                raise CommitUnknown("database_migration_unknown") from error
            raise

    try:
        return await pending.run(apply, write=True)
    finally:
        await pending.aclose()
