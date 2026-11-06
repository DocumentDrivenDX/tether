"""Explicit exportable migrations; ordinary root construction only verifies them."""

import hashlib
import re
from typing import LiteralString, cast
from uuid import UUID

from psycopg import AsyncConnection, sql
from psycopg.pq import TransactionStatus

from tether.types.execution import Actor, CommitUnknown, copy_actor
from tether.types.storage import IntegrityError, StorageLimits, StoreError

from .durability import NATIVE_POSTGRES_PROFILE, PostgresDurabilityProfile
from .io import PendingIO
from .profiles import Table, verify_tables
from .revision2 import DDL as EXECUTION_DDL
from .revision2 import TABLES as EXECUTION_TABLES
from .source import ConnectionSource

REVISION = 2
_DDL = """CREATE TABLE {schema}.history (
    owner bytea NOT NULL, session bytea NOT NULL, key bytea NOT NULL,
    envelope bytea NOT NULL, sha256 text NOT NULL,
    PRIMARY KEY (owner, session, key))"""
CHECKSUM_V1 = hashlib.sha256(_DDL.encode()).hexdigest()
CHECKSUM = hashlib.sha256((CHECKSUM_V1 + "\n" + "\n".join(EXECUTION_DDL)).encode()).hexdigest()
CHECKSUMS = {1: CHECKSUM_V1, 2: CHECKSUM}
BASE_TABLES = (
    Table(
        "metadata",
        (
            ("singleton", "boolean", True),
            ("namespace", "uuid", True),
            ("revision", "integer", True),
            ("checksum", "text", True),
        ),
        ("singleton",),
    ),
    Table(
        "history",
        (
            ("owner", "bytea", True),
            ("session", "bytea", True),
            ("key", "bytea", True),
            ("envelope", "bytea", True),
            ("sha256", "text", True),
        ),
        ("owner", "session", "key"),
    ),
)


def qualified(schema: str) -> str:
    if re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema) is None:
        raise ValueError("invalid_database_schema")
    return '"' + schema + '"'


def migration_sql(schema: str, *, revision: int = REVISION) -> tuple[str, ...]:
    name = qualified(schema)
    if revision not in CHECKSUMS:
        raise ValueError("unsupported_database_revision")
    return (
        f"CREATE SCHEMA IF NOT EXISTS {name}",
        f"CREATE TABLE {name}.metadata ("
        "singleton boolean PRIMARY KEY CHECK (singleton), namespace uuid NOT NULL, "
        "revision integer NOT NULL, checksum text NOT NULL)",
        _DDL.format(schema=name),
        *(statement.format(schema=name) for statement in EXECUTION_DDL if revision == 2),
        sql.SQL(
            "INSERT INTO {}.metadata VALUES (true, gen_random_uuid(), {}, {}) "
            "ON CONFLICT (singleton) DO NOTHING"
        )
        .format(sql.Identifier(schema), sql.Literal(revision), sql.Literal(CHECKSUMS[revision]))
        .as_string(),
    )


def idle(connection: AsyncConnection[tuple[object, ...]]) -> None:
    if connection.closed:
        raise StoreError("database_unavailable")
    if connection.info.transaction_status != TransactionStatus.IDLE:
        raise IntegrityError("ambient_database_transaction")
    if connection.info.server_version // 10000 != 18:
        raise IntegrityError("unsupported_database_version")


async def inspect(
    connection: AsyncConnection[tuple[object, ...]],
    schema: str,
    *,
    revision: int = REVISION,
    durability_profile: PostgresDurabilityProfile = NATIVE_POSTGRES_PROFILE,
) -> UUID:
    await verify_tables(connection, schema, BASE_TABLES)
    cursor = await connection.execute(
        sql.SQL(
            "SELECT singleton, namespace, revision, checksum, current_setting('fsync'), "
            "current_setting('full_page_writes'), current_setting('synchronous_commit'), "
            "current_setting('server_version_num') "
            "FROM {}.metadata"
        ).format(sql.Identifier(schema))
    )
    rows = await cursor.fetchall()
    if len(rows) != 1:
        raise IntegrityError("database_revision_mismatch")
    row = rows[0]
    if (
        row[0] is not True
        or row[2:4] != (revision, CHECKSUMS[revision])
        or not isinstance(row[1], UUID)
    ):
        raise IntegrityError("database_revision_mismatch")
    settings = row[4:7]
    if durability_profile.name == "native" and settings != ("on", "on", "on"):
        raise IntegrityError("unsupported_database_durability")
    if durability_profile.name == "databricks-lakebase-pg18" and settings != ("off", "off", "on"):
        raise IntegrityError("unsupported_managed_database_durability")
    raw_server_version = row[7]
    if isinstance(raw_server_version, bool) or not isinstance(raw_server_version, (int, str)):
        raise IntegrityError("unsupported_database_version") from None
    try:
        server_version = int(raw_server_version)
    except ValueError:
        raise IntegrityError("unsupported_database_version") from None
    if server_version // 10000 != 18:
        raise IntegrityError("unsupported_database_version")
    if revision == 2:
        await verify_tables(connection, schema, EXECUTION_TABLES)
    return row[1]


def upgrade_sql(schema: str) -> tuple[str, ...]:
    name = qualified(schema)
    update = (
        sql.SQL("UPDATE {}.metadata SET revision={}, checksum={} WHERE singleton")
        .format(
            sql.Identifier(schema),
            sql.Literal(REVISION),
            sql.Literal(CHECKSUM),
        )
        .as_string()
    )
    return (*(statement.format(schema=name) for statement in EXECUTION_DDL), update)


async def _advance(
    connection: AsyncConnection[tuple[object, ...]],
    schema: str,
    target: int,
    durability_profile: PostgresDurabilityProfile,
) -> UUID:
    await verify_tables(connection, schema, BASE_TABLES)
    cursor = await connection.execute(
        sql.SQL("SELECT revision FROM {}.metadata WHERE singleton").format(sql.Identifier(schema))
    )
    row = await cursor.fetchone()
    if row == (1,) and target == 2:
        await inspect(connection, schema, revision=1, durability_profile=durability_profile)
        for statement in upgrade_sql(schema):
            await connection.execute(sql.SQL(cast(LiteralString, statement)))
    return await inspect(connection, schema, revision=target, durability_profile=durability_profile)


async def migrate(
    source: ConnectionSource,
    *,
    actor: Actor,
    schema: str,
    limits: StorageLimits | None = None,
    revision: int = REVISION,
    durability_profile: PostgresDurabilityProfile = NATIVE_POSTGRES_PROFILE,
) -> UUID:
    if (
        durability_profile.name != "native"
        and getattr(source, "durability_profile", None) != durability_profile
    ):
        raise IntegrityError("postgres_profile_attestation_mismatch")
    statements = migration_sql(schema, revision=revision)
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
                        identity = await _advance(connection, schema, revision, durability_profile)
                    else:
                        occupied = await connection.execute(
                            "SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_class c "
                            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                            "WHERE n.nspname=%s AND c.relname=ANY(%s))",
                            (schema, [table.name for table in (*BASE_TABLES, *EXECUTION_TABLES)]),
                        )
                        if await occupied.fetchone() != (False,):
                            raise IntegrityError("unmanaged_database_schema")
                        for statement in statements:
                            await connection.execute(sql.SQL(cast(LiteralString, statement)))
                        identity = await inspect(
                            connection,
                            schema,
                            revision=revision,
                            durability_profile=durability_profile,
                        )
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
