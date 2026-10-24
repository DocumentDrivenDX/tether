"""SQL metadata paging matches the shared pure oracle, including Unicode and NUL."""

from datetime import timedelta
from uuid import uuid4

import pytest
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from psycopg import sql
from test_postgres_primitives import append

from tether.stores.postgres import DatabaseRoot
from tether.stores.postgres.journal import Journal
from tether.stores.postgres.listing import Listing
from tether.stores.queries import page
from tether.types.execution import Actor, ExecutionDenied, NewEntry
from tether.types.storage import (
    IntegrityError,
    SessionInit,
    SessionQuery,
    StorageLimits,
    StoreError,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def seed(h):
    journal = Journal(h.root, h.actor)
    summaries = []
    for i in range(6):
        summary = await journal.create(
            SessionInit(
                ref=f"ref-{i}" + chr(0),
                title=("Straße" if i % 2 else "Fabricated") + str(i) + chr(0),
                tags=("shared", "tag" + chr(0)) if i % 2 else ("shared",),
            ),
            str(uuid4()),
        )
        if i % 3:
            _, frame = await append(
                journal,
                summary.id,
                [
                    NewEntry(
                        kind="turn_started",
                        turn_id=f"turn-{i}",
                        facts={"spec_id": "demo", "spec_digest": "digest", "spec_version": "1"},
                    )
                ],
            )
            summary = frame.projection.summary
        summaries.append(summary)
    return summaries


@pytest.mark.parametrize("order", ["created_asc", "created_desc", "updated_desc"])
@pytest.mark.parametrize("filter_name", ["all", "status", "tags", "text", "ref", "dates"])
async def test_paging_and_filters_match_the_shared_metadata_oracle(database, order, filter_name):
    h = database
    summaries = await seed(h)
    filters = {
        "all": {},
        "status": {"status": frozenset({"idle"})},
        "tags": {"tags_all": ("tag" + chr(0),)},
        "text": {"text": "STRASSE"},
        "ref": {"ref": "ref-3" + chr(0)},
        "dates": {
            "updated_after": summaries[1].updated_at,
            "updated_before": max(s.updated_at for s in summaries) + timedelta(seconds=1),
        },
    }
    query = SessionQuery(owner=h.actor.id, order=order, **filters[filter_name])
    listing = Listing(h.root, h.actor)
    cursor = None
    seen = []
    while True:
        expected = page(summaries, query, cursor, 2)
        actual = await listing.list(query, cursor=cursor, limit=2)
        assert actual == expected
        seen.extend(actual.items)
        cursor = actual.next
        if cursor is None:
            break
    assert len({s.id for s in seen}) == len(seen)


async def test_owner_reference_identity_and_unknown_status_are_not_text_sql_inputs(database):
    h = database
    summaries = await seed(h)
    listing = Listing(h.root, h.actor)
    assert await listing.find("ref-3" + chr(0)) == summaries[3]
    assert await listing.find("absent") is None
    assert not (
        await listing.list(SessionQuery(owner=h.actor.id, status=frozenset({chr(0)})))
    ).items
    other = Actor(id="different-owner")
    other_summary = await Journal(h.root, other).create(
        SessionInit(ref="ref-3" + chr(0)), str(uuid4())
    )
    assert await Listing(h.root, other).find("ref-3" + chr(0)) == other_summary
    before = len(h.calls)
    with pytest.raises(ExecutionDenied):
        await listing.list(SessionQuery(owner=other.id))
    assert len(h.calls) == before


async def test_metadata_lookahead_skips_payload_but_selected_summary_aggregate_is_bounded(database):
    h = database
    journal = Journal(h.root, h.actor)
    small = await journal.create(SessionInit(title="small"), str(uuid4()))
    await journal.create(SessionInit(title="x" * 12001), str(uuid4()))
    root = await DatabaseRoot.open(
        h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(max_batch_bytes=12000)
    )
    query = SessionQuery(owner=h.actor.id, order="created_asc")
    try:
        listing = Listing(root, h.actor)
        first = await listing.list(query, limit=1)
        assert first.items == (small,)
        assert first.next is not None
        with pytest.raises(StoreError, match="list_read_bytes"):
            await listing.list(query, cursor=first.next, limit=1)
        with pytest.raises(StoreError, match="list_read_bytes"):
            await listing.list(query, limit=2)
    finally:
        assert await root.aclose()


async def test_listing_rejects_cached_metadata_drift_and_invalid_cursor_before_acquisition(
    database,
):
    h = database
    summaries = await seed(h)
    listing = Listing(h.root, h.actor)
    query = SessionQuery(owner=h.actor.id)
    before = len(h.calls)
    with pytest.raises(ValueError, match="invalid_cursor"):
        await listing.list(query, cursor="fabricated-invalid")
    assert len(h.calls) == before
    async with h.pool.connection() as connection:
        await connection.execute(
            sql.SQL("UPDATE {}.sessions SET tags=%s WHERE owner=%s AND id=%s").format(
                sql.Identifier(h.schema)
            ),
            ([b""] * 12001, h.actor.id.encode(), summaries[0].id.encode()),
        )
    with pytest.raises(IntegrityError, match="projection_integrity"):
        await listing.list(query)
