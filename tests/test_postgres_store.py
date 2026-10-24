"""Public SQL caller binding, bounded read ports and maintenance isolation."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from test_client_resume import FreshCredential
from test_postgres_leases import start
from test_postgres_read_integrity import snapshot

from tether.stores.postgres import DatabaseRoot
from tether.stores.postgres.capture import Capture
from tether.stores.postgres.store import PostgresSessionStore
from tether.types.execution import Actor, ExecutionDenied, NewEntry, WorkerOutstanding
from tether.types.storage import (
    SessionInit,
    SessionPatch,
    SessionQuery,
    StorageLimits,
    StoreError,
    Unsupported,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_caller_scope_reads_writes_and_borrowed_close(database):
    h = database
    root_store = PostgresSessionStore(h.root)
    store = root_store.for_actor(h.actor)
    with pytest.raises(ExecutionDenied, match="caller_scope_required"):
        await root_store.create(h.actor.id, SessionInit())
    session = await store.create(h.actor.id, SessionInit(ref="ref", title="Title"))
    assert (await store.create(h.actor.id, SessionInit(ref="ref"))).id == session.id
    async with store.turn(session.id, h.actor.id, turn_id="job") as log:
        await log.append([NewEntry(kind="message", key="original", turn_id="phase")])
    updated = await store.update(session.id, h.actor.id, SessionPatch(tags=("tag",)))
    assert updated.title == "Title"
    assert updated.tags == ("tag",)
    assert (await store.update(session.id, h.actor.id, SessionPatch(title=None))).title is None
    view = await store.get(session.id, h.actor.id)
    assert view is not None
    assert view.summary.id == session.id
    found = await store.find(h.actor.id, "ref")
    assert found is not None
    assert found.id == session.id
    assert (await store.list(SessionQuery(owner=h.actor.id))).items[0].id == session.id
    assert (await store.snapshot(session.id, h.actor.id)).through_index == 2
    assert (await store.entries(session.id, h.actor.id, start=0, limit=1)).next_start == 1
    assert (await store.lookup(session.id, h.actor.id, kind="key", value="original"))[0].index == 0
    assert (await store.effects(session.id, h.actor.id)).items == ()
    feed = store.changes(h.actor.id, session_id=session.id, since=0)
    try:
        assert (await anext(feed)).seq == 1
    finally:
        await feed.aclose()
    assert await store.aclose()
    assert not h.pool.closed
    with pytest.raises(StoreError, match="store_scope_closed"):
        await store.watermark(h.actor.id)
    assert await root_store.for_actor(h.actor).watermark(h.actor.id) == 4


@pytest.mark.parametrize("operation", ["get", "list", "rebind", "turn", "recover", "factory"])
async def test_scope_refuses_foreign_owner_or_global_authority_before_acquisition(
    database, operation
):
    h = database
    root_store = PostgresSessionStore(h.root, maintenance=Actor(id="maintenance"))
    store = root_store.for_actor(h.actor)
    before = len(h.calls)
    with pytest.raises((ExecutionDenied, Unsupported)):
        await refuse_operation(operation, store, root_store, h.actor)
    assert len(h.calls) == before


async def refuse_operation(operation, store, root_store, actor):
    if operation == "get":
        await store.get("session", "foreign")
    elif operation == "list":
        await store.list(SessionQuery(owner="foreign"))
    elif operation == "rebind":
        store.for_actor(Actor(id="foreign"))
    elif operation == "turn":
        async with store.turn("session", "foreign", turn_id="job"):
            raise AssertionError("entered foreign turn")
    elif operation == "recover":
        await store.recover(now=datetime.now(UTC))
    else:
        await root_store.get("session", actor.id)


async def test_same_owner_rebinding_preserves_current_credentials_and_isolates_actor(database):
    h = database
    creds = [FreshCredential(), FreshCredential()]
    actors = [
        Actor(id=h.actor.id, principal=str(i), attributes={"mutable": [i]}, credential=creds[i])
        for i in range(2)
    ]
    seen = []

    @asynccontextmanager
    async def source(actor, session_id):
        seen.append((actor.id, actor.principal, actor.attributes.copy(), actor.credential))
        actor.attributes["mutable"] = [99]
        async with h.source(actor, session_id) as connection:
            yield connection

    root = await DatabaseRoot.open(source, actor=h.actor, schema=h.schema)
    store = PostgresSessionStore(root)
    try:
        first = store.for_actor(actors[0])
        second = first.for_actor(actors[1])
        seen.clear()
        await asyncio.gather(first.watermark(h.actor.id), second.watermark(h.actor.id))
        assert {value[1] for value in seen} == {"0", "1"}
        assert {id(value[3]) for value in seen} == {id(c) for c in creds}
        assert actors[0].attributes == {"mutable": [0]}
        assert actors[1].attributes == {"mutable": [1]}
        assert await first.aclose()
        assert await second.watermark(h.actor.id) == 0
    finally:
        assert await root.aclose()


async def seed_abandoned(h, actor):
    store = Capture(h.root, actor)
    session = await store.create(SessionInit(), str(uuid4()))
    token, _ = await store.acquire(session.id, turn_id="job")
    await store.append_fenced(token, [start("original-phase")])
    async with h.pool.connection() as connection:
        from psycopg import sql

        await connection.execute(
            sql.SQL(
                "UPDATE {}.sessions SET lease_until=clock_timestamp()-interval '1 second' "
                "WHERE owner=%s AND id=%s"
            ).format(sql.Identifier(h.schema)),
            (actor.id.encode(), session.id.encode()),
        )
    return session


async def test_global_recovery_keeps_maintenance_actor_separate_from_row_owners(database):
    h = database
    actors = [h.actor, Actor(id="second-owner")]
    sessions = [await seed_abandoned(h, actor) for actor in actors]
    maintenance = Actor(id="maintenance", credential=FreshCredential())
    store = PostgresSessionStore(h.root, maintenance=maintenance)
    h.calls.clear()
    recovered = await store.recover(now=datetime.now(UTC) + timedelta(days=1))
    assert {r.session_id for r in recovered} == {s.id for s in sessions}
    assert {r.turn_id for r in recovered} == {"original-phase"}
    assert all(actor.id == maintenance.id for actor, _ in h.calls)
    assert all(actor.credential is maintenance.credential for actor, _ in h.calls)
    assert all(sid is None for _, sid in h.calls)
    assert await store.recover(now=datetime.now(UTC)) == ()
    for actor, session in zip(actors, sessions, strict=True):
        view = await store.for_actor(actor).get(session.id, actor.id)
        assert view is not None
        assert view.summary.owner == actor.id
        assert view.summary.status == "interrupted"


async def test_owner_recovery_does_not_scan_or_mutate_foreign_sessions(database):
    h = database
    ours = await seed_abandoned(h, h.actor)
    foreign = await seed_abandoned(h, Actor(id="foreign"))
    store = PostgresSessionStore(h.root, actor=h.actor)
    h.calls.clear()
    recovered = await store.recover_owner(now=datetime.now(UTC))
    assert [r.session_id for r in recovered] == [ours.id]
    assert all(actor.id == h.actor.id for actor, _ in h.calls)
    other = PostgresSessionStore(h.root, actor=Actor(id="foreign"))
    view = await other.get(foreign.id, "foreign")
    assert view is not None
    assert view.summary.status == "running"
    assert len(await other.recover_owner(now=datetime.now(UTC))) == 1


async def test_recovery_is_one_commit_and_live_workers_roll_back_all_candidates(database):
    h = database
    sessions = [await seed_abandoned(h, h.actor) for _ in range(2)]
    future = asyncio.get_running_loop().create_future()
    h.root.workers.retain(sessions[-1].id, future)
    before = await snapshot(h)
    try:
        with pytest.raises(WorkerOutstanding):
            await PostgresSessionStore(h.root, maintenance=Actor(id="maintenance")).recover(
                now=datetime.now(UTC)
            )
        assert await snapshot(h) == before
    finally:
        future.set_result(None)
        await asyncio.sleep(0)
    assert (
        len(await PostgresSessionStore(h.root, actor=h.actor).recover_owner(now=datetime.now(UTC)))
        == 2
    )


async def test_recovery_candidate_limit_refuses_without_partial_publication(database):
    h = database
    for _ in range(2):
        await seed_abandoned(h, h.actor)
    root = await DatabaseRoot.open(
        h.source,
        actor=h.actor,
        schema=h.schema,
        limits=StorageLimits(page_limit=1, max_startup_batches=1),
    )
    before = await snapshot(h)
    try:
        store = PostgresSessionStore(root, maintenance=Actor(id="maintenance"))
        with pytest.raises(StoreError, match="recovery_candidate_count"):
            await store.recover(now=datetime.now(UTC))
        assert await snapshot(h) == before
    finally:
        assert await root.aclose()


async def test_closed_root_refuses_factories_before_any_new_acquisition(database):
    h = database
    root = await DatabaseRoot.open(h.source, actor=h.actor, schema=h.schema)
    store = PostgresSessionStore(root)
    assert await store.aclose()
    before = len(h.calls)
    with pytest.raises(StoreError, match="database_root_closed"):
        store.for_actor(h.actor)
    assert len(h.calls) == before


async def test_create_ref_is_owner_scoped_acquisition_not_generated_session_scope(database):
    h = database
    seen = []

    @asynccontextmanager
    async def source(actor, sid):
        assert sid is None
        seen.append(actor.id)
        async with h.source(actor, sid) as connection:
            yield connection

    root = await DatabaseRoot.open(source, actor=h.actor, schema=h.schema)
    try:
        store = PostgresSessionStore(root).for_actor(h.actor)
        first = await store.create(h.actor.id, SessionInit(ref="same"))
        assert (await store.create(h.actor.id, SessionInit(ref="same"))).id == first.id
        assert seen == [h.actor.id] * 3
    finally:
        assert await root.aclose()


@pytest.mark.parametrize("identity", [b"\xff", b"x" * 10000])
async def test_create_ref_refuses_invalid_or_oversized_cached_identity(database, identity):
    h = database
    from psycopg import sql

    store = PostgresSessionStore(h.root).for_actor(h.actor)
    session = await store.create(h.actor.id, SessionInit(ref="corrupt"))
    async with h.pool.connection() as connection:
        await connection.execute(
            sql.SQL("UPDATE {}.sessions SET id=%s WHERE owner=%s AND id=%s").format(
                sql.Identifier(h.schema)
            ),
            (identity, h.actor.id.encode(), session.id.encode()),
        )
    root = await DatabaseRoot.open(
        h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(max_batch_bytes=5000)
    )
    from tether.types.storage import IntegrityError

    try:
        scoped = PostgresSessionStore(root).for_actor(h.actor)
        before = await snapshot(h)
        with pytest.raises((IntegrityError, StoreError), match="session_identity_"):
            await scoped.create(h.actor.id, SessionInit(ref="corrupt"))
        assert await snapshot(h) == before
    finally:
        assert await root.aclose()


async def test_recovery_result_bytes_roll_back_earlier_candidates(database):
    h = database
    for _ in range(2):
        store = Capture(h.root, h.actor)
        session = await store.create(SessionInit(), str(uuid4()))
        token, _ = await store.acquire(session.id, turn_id="job")
        await store.append_fenced(token, [start("phase:" + "x" * 3000)])
        from test_postgres_leases import expire

        await expire(h, session.id)
    root = await DatabaseRoot.open(
        h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(max_batch_bytes=5000)
    )
    before = await snapshot(h)
    try:
        with pytest.raises(StoreError, match="recovery_result_bytes"):
            await PostgresSessionStore(root, maintenance=Actor(id="maintenance")).recover(
                now=datetime.now(UTC)
            )
        assert await snapshot(h) == before
    finally:
        assert await root.aclose()


async def test_recovery_sql_stops_transfer_at_first_byte_overflow(database, monkeypatch):
    h = database
    for _ in range(4):
        await seed_abandoned(h, h.actor)
    root = await DatabaseRoot.open(
        h.source,
        actor=h.actor,
        schema=h.schema,
        limits=StorageLimits(max_batch_bytes=60, page_limit=10),
    )
    from psycopg import AsyncConnection, sql

    original = AsyncConnection.execute
    transferred = []

    async def observe(connection, query, *args, **kwargs):
        cursor = await original(connection, query, *args, **kwargs)
        if isinstance(query, sql.Composable) and "lease_until<=" in query.as_string(connection):
            rows = await cursor.fetchall()
            transferred.append(len(rows))
            await cursor.scroll(0, mode="absolute")
        return cursor

    monkeypatch.setattr(AsyncConnection, "execute", observe)
    before = await snapshot(h)
    try:
        with pytest.raises(StoreError, match="recovery_candidate_bytes"):
            await PostgresSessionStore(root, maintenance=Actor(id="maintenance")).recover(
                now=datetime.now(UTC)
            )
        assert transferred == [2]
        assert await snapshot(h) == before
    finally:
        assert await root.aclose()


@pytest.mark.parametrize("operation", ["factory", "turn_factory", "read", "close"])
async def test_public_scope_wrong_loop_refuses_before_source_or_state_change(database, operation):
    h = database
    factory = PostgresSessionStore(h.root)
    store = factory.for_actor(h.actor)
    before = len(h.calls)

    async def invoke():
        if operation == "factory":
            store.for_actor(h.actor)
        elif operation == "turn_factory":
            store.turn("session", h.actor.id, turn_id="job")
        elif operation == "read":
            await store.get("session", h.actor.id)
        else:
            await store.aclose()

    def foreign():
        with pytest.raises(RuntimeError, match="wrong_event_loop"):
            asyncio.run(invoke())

    await asyncio.to_thread(foreign)
    assert len(h.calls) == before
    assert await store.watermark(h.actor.id) == 0
