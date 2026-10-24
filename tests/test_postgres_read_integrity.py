"""Corrupted indexed rows cannot widen metadata transfer or pending authority."""

from uuid import uuid4

import pytest
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from psycopg import sql
from test_postgres_primitives import append
from test_postgres_restore_primitives import pending_values, view

from tether.stores.postgres import DatabaseRoot
from tether.stores.postgres.journal import Journal
from tether.types.canonical import canonical
from tether.types.execution import Entry
from tether.types.storage import (
    IntegrityError,
    SessionInit,
    StorageLimits,
    StoredProjection,
    StoreError,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def snapshot(h):
    result = []
    async with h.pool.connection() as connection:
        for name in ("sessions", "entries", "owner_heads", "changes", "pending", "sources"):
            cursor = await connection.execute(
                sql.SQL("SELECT * FROM {}.{}").format(
                    sql.Identifier(h.schema), sql.Identifier(name)
                )
            )
            result.append(sorted(await cursor.fetchall(), key=repr))
    return result


@pytest.mark.parametrize("column", ["status", "ref", "title_fold", "tags", "empty_tags"])
async def test_redundant_metadata_is_compared_in_sql_without_transfer(database, column):
    h = database
    journal = Journal(h.root, h.actor)
    summary = await journal.create(SessionInit(), str(uuid4()))
    column_name = "tags" if column == "empty_tags" else column
    corruptions = {
        "status": "x" * 12001,
        "ref": b"x" * 12001,
        "title_fold": b"x" * 12001,
        "tags": [b"x" * 12001],
        "empty_tags": [b""] * 12001,
    }
    async with h.pool.connection() as connection:
        await connection.execute(
            sql.SQL("UPDATE {}.sessions SET {}=%s WHERE owner=%s AND id=%s").format(
                sql.Identifier(h.schema), sql.Identifier(column_name)
            ),
            (corruptions[column], journal.owner, summary.id.encode()),
        )
    before = await snapshot(h)
    root = await DatabaseRoot.open(
        h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(max_batch_bytes=12000)
    )
    try:
        with pytest.raises(IntegrityError, match="projection_integrity"):
            await view(Journal(root, h.actor), summary.id)
        assert await snapshot(h) == before
    finally:
        assert await root.aclose()


@pytest.mark.parametrize("encoding", ["oversized", "invalid_utf8"])
async def test_lease_identity_is_bounded_and_invalid_utf8_is_typed(database, encoding):
    h = database
    journal = Journal(h.root, h.actor)
    summary = await journal.create(SessionInit(), str(uuid4()))
    data = b"x" * 12001 if encoding == "oversized" else bytes([255])
    async with h.pool.connection() as connection:
        await connection.execute(
            sql.SQL(
                "UPDATE {}.sessions SET lease_turn=%s,lease_until=clock_timestamp(),"
                "generation=1 WHERE owner=%s AND id=%s"
            ).format(sql.Identifier(h.schema)),
            (data, journal.owner, summary.id.encode()),
        )
    before = await snapshot(h)
    root = await DatabaseRoot.open(
        h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(max_batch_bytes=12000)
    )
    try:
        reason = "projection_read_bytes" if encoding == "oversized" else "lease_integrity"
        error = StoreError if encoding == "oversized" else IntegrityError
        with pytest.raises(error, match=reason):
            await view(Journal(root, h.actor), summary.id)
        assert await snapshot(h) == before
    finally:
        assert await root.aclose()


@pytest.mark.parametrize("column", ["summary", "projection"])
@pytest.mark.parametrize("encoding", ["whitespace", "omitted_defaults"])
async def test_parseable_noncanonical_projection_refuses(database, column, encoding):
    h = database
    journal = Journal(h.root, h.actor)
    summary = await journal.create(SessionInit(), str(uuid4()))
    async with h.pool.connection() as connection:
        cursor = await connection.execute(
            sql.SQL("SELECT {} FROM {}.sessions WHERE owner=%s AND id=%s").format(
                sql.Identifier(column), sql.Identifier(h.schema)
            ),
            (journal.owner, summary.id.encode()),
        )
        original = (await cursor.fetchone())[0]
        if encoding == "whitespace":
            changed = b" " + original
        else:
            model = type(summary) if column == "summary" else StoredProjection
            value = model.model_validate_json(original)
            changed = canonical(value.model_dump(mode="json", exclude_defaults=True))
        assert original != changed
        await connection.execute(
            sql.SQL("UPDATE {}.sessions SET {}=%s WHERE owner=%s AND id=%s").format(
                sql.Identifier(h.schema), sql.Identifier(column)
            ),
            (changed, journal.owner, summary.id.encode()),
        )
    before = await snapshot(h)
    with pytest.raises(IntegrityError, match="projection_integrity"):
        await view(journal, summary.id)
    assert await snapshot(h) == before


async def test_oversized_pending_invocation_refuses_without_mutation(database):
    h = database
    journal = Journal(h.root, h.actor)
    summary = await journal.create(SessionInit(), str(uuid4()))
    values, invocations = pending_values(summary, h.actor)
    await append(journal, summary.id, values)
    root = await DatabaseRoot.open(
        h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(max_batch_bytes=12000)
    )
    try:
        assert (await view(Journal(root, h.actor), summary.id)).pending is not None
        async with h.pool.connection() as connection:
            await connection.execute(
                sql.SQL(
                    "UPDATE {}.pending SET invocation=%s WHERE owner=%s AND invocation=%s"
                ).format(sql.Identifier(h.schema)),
                (b"x" * 12001, journal.owner, invocations[0].encode()),
            )
        before = await snapshot(h)
        with pytest.raises(StoreError, match="projection_read_bytes"):
            await view(Journal(root, h.actor), summary.id)
        assert await snapshot(h) == before
    finally:
        assert await root.aclose()


def mutate(value, corruption):
    value = value.model_dump(mode="json")
    if corruption == "index":
        value["index"] = 999
    elif corruption == "kind":
        value["kind"] = "message"
    elif corruption == "state":
        value["facts"]["state"] = "approved"
    elif corruption in {"invocation_id", "session_id", "actor_id"} and "binding" in value["facts"]:
        value["facts"]["binding"][corruption] = "different"
    else:
        value["history_ref"][corruption] = "different"
    return canonical(Entry.model_validate(value).model_dump(mode="json"))


@pytest.mark.parametrize(
    ("target", "corruption"),
    [
        ("requested", "index"),
        ("source", "index"),
        ("requested", "kind"),
        ("requested", "state"),
        ("requested", "invocation_id"),
        ("requested", "session_id"),
        ("requested", "actor_id"),
        ("source", "owner"),
        ("source", "session_id"),
        ("requested", "whitespace"),
        ("source", "whitespace"),
    ],
)
async def test_pending_requires_canonical_scoped_positional_identity(database, target, corruption):
    h = database
    journal = Journal(h.root, h.actor)
    summary = await journal.create(SessionInit(), str(uuid4()))
    values, _ = pending_values(summary, h.actor)
    accepted, _ = await append(journal, summary.id, values)
    original = accepted[4 if target == "requested" else 1]
    data = canonical(original.model_dump(mode="json"))
    changed = b" " + data if corruption == "whitespace" else mutate(original, corruption)
    async with h.pool.connection() as connection:
        await connection.execute(
            sql.SQL(
                "UPDATE {}.entries SET envelope=%s WHERE owner=%s AND session=%s AND position=%s"
            ).format(sql.Identifier(h.schema)),
            (changed, journal.owner, summary.id.encode(), original.index),
        )
    before = await snapshot(h)
    with pytest.raises(IntegrityError):
        await view(journal, summary.id)
    assert await snapshot(h) == before
