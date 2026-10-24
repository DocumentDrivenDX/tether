"""Real PostgreSQL receipt frontiers, actor propagation and uncertain acknowledgment."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from test_client_resume import FreshCredential
from test_postgres_leases import expire, start

from tether.stores.postgres import DatabaseRoot, PostgresSessionStore
from tether.stores.postgres.leases import Leases
from tether.testing.conformance import LiveStoreFixture, mutation_receipt_suite
from tether.types.execution import Actor, CommitUnknown, ExecutionDenied
from tether.types.mutations import receipt_store
from tether.types.storage import SessionInit, SessionPatch, StoreError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_public_sql_receipt_suite(database):
    h = database

    @asynccontextmanager
    async def factory():
        yield LiveStoreFixture(PostgresSessionStore(h.root), h.actor, Actor(id="foreign"))

    report = await mutation_receipt_suite(factory)
    assert report.capabilities["mutation_receipts"] == 1
    assert "exact_accepted_metadata" in report.checks


async def test_sql_receipt_credentials_and_foreign_owner_gate(
    database,
):
    h = database
    credential = FreshCredential()
    caller = h.actor.model_copy(update={"credential": credential, "principal": "current"})
    scope = PostgresSessionStore(h.root).for_actor(caller)
    selected = receipt_store(scope)
    assert selected is not None
    h.calls.clear()
    with pytest.raises(ExecutionDenied):
        await selected.create_with_receipt("foreign", SessionInit())
    with pytest.raises(ExecutionDenied):
        await selected.update_with_receipt("missing", "foreign", SessionPatch())
    assert not h.calls
    made = await selected.create_with_receipt(caller.id, SessionInit())
    changed = await selected.update_with_receipt(
        made.summary.id, caller.id, SessionPatch(title="Fabricated")
    )
    assert changed.commit is not None
    assert changed.summary.entry_count == 1
    assert h.calls
    assert all(
        actor.credential is credential and actor.principal == "current" for actor, _ in h.calls
    )
    assert await scope.aclose()
    with pytest.raises(StoreError, match="scope_closed"):
        await selected.create_with_receipt(caller.id, SessionInit())


async def test_sql_concurrent_updates_have_exact_distinct_receipt_frontiers(database):
    h = database
    store = PostgresSessionStore(h.root).for_actor(h.actor)
    selected = receipt_store(store)
    assert selected is not None
    made = await selected.create_with_receipt(h.actor.id, SessionInit())
    # Distinct sessions allow both transactions to proceed without turn-lease refusal.
    other = await selected.create_with_receipt(h.actor.id, SessionInit())
    receipts = await asyncio.gather(
        *[
            selected.update_with_receipt(sid, h.actor.id, SessionPatch(title=title))
            for sid, title in ((made.summary.id, "First"), (other.summary.id, "Second"))
        ]
    )
    assert receipts[0].summary.title == "First"
    assert receipts[1].summary.title == "Second"
    assert receipts[0].summary.last_seq != receipts[1].summary.last_seq
    for receipt in receipts:
        assert receipt.commit is not None
        assert receipt.commit.seq == receipt.summary.last_seq
        page = await store.entries(receipt.summary.id, h.actor.id, start=0, limit=3)
        assert page.items == receipt.commit.entries


async def test_sql_unknown_release_after_creation_returns_no_receipt(database):
    h, fail = database, False

    @asynccontextmanager
    async def source(actor, session_id):
        async with h.pool.connection() as connection:
            yield connection
        if fail:
            raise RuntimeError("fabricated release failure")

    root = await DatabaseRoot.open(source, actor=h.actor, schema=h.schema)
    scope = PostgresSessionStore(root).for_actor(h.actor)
    try:
        fail = True
        with pytest.raises(CommitUnknown, match="release_unknown"):
            await scope.create_with_receipt(h.actor.id, SessionInit(ref="fabricated"))
        fail = False
        known = await scope.create_with_receipt(h.actor.id, SessionInit(ref="fabricated"))
        assert known.disposition == "existing"
        assert known.commit is None
        assert known.summary.entry_count == 0
    finally:
        fail = False
        assert await root.aclose()


async def test_sql_receipt_does_not_follow_a_later_update_during_release(database, monkeypatch):
    h, fired = database, False
    store = PostgresSessionStore(h.root).for_actor(h.actor)
    made = await store.create_with_receipt(h.actor.id, SessionInit())
    original = Leases.release

    async def release(self, token):
        nonlocal fired
        await original(self, token)
        if not fired:
            fired = True
            await store.update_with_receipt(
                made.summary.id, h.actor.id, SessionPatch(title="Later")
            )

    monkeypatch.setattr(Leases, "release", release)
    first = await store.update_with_receipt(
        made.summary.id, h.actor.id, SessionPatch(title="First")
    )
    assert first.summary.title == "First"
    assert first.summary.entry_count == 1
    latest = await store.get(made.summary.id, h.actor.id)
    assert latest is not None
    assert latest.summary.title == "Later"
    assert latest.summary.last_seq > first.summary.last_seq


async def test_sql_noop_does_not_claim_expired_phase_recovery_as_metadata(database):
    h = database
    store = PostgresSessionStore(h.root).for_actor(h.actor)
    made = await store.create_with_receipt(h.actor.id, SessionInit())
    leases = Leases(h.root, h.actor)
    token, _ = await leases.acquire(made.summary.id, turn_id="old")
    await leases.append_fenced(token, [start("old")])
    before = await store.snapshot(made.summary.id, h.actor.id)
    await expire(h, made.summary.id)
    receipt = await store.update_with_receipt(made.summary.id, h.actor.id, SessionPatch())
    assert receipt.disposition == "noop"
    assert receipt.commit is None
    assert receipt.summary.last_seq > before.view.summary.last_seq
    assert receipt.summary.status == "interrupted"
    assert receipt.summary.resumable
    assert receipt.summary.entry_count == before.view.summary.entry_count + 1


async def test_sql_unknown_ack_after_metadata_commit_returns_no_receipt(database):
    h, armed, releases = database, False, 0

    @asynccontextmanager
    async def source(actor, session_id):
        nonlocal releases
        async with h.pool.connection() as connection:
            yield connection
        if armed and session_id is not None:
            releases += 1
            if releases == 2:
                raise RuntimeError("fabricated metadata acknowledgment loss")

    root = await DatabaseRoot.open(source, actor=h.actor, schema=h.schema)
    scope = PostgresSessionStore(root).for_actor(h.actor)
    try:
        made = await scope.create_with_receipt(h.actor.id, SessionInit())
        armed = True
        with pytest.raises(CommitUnknown, match="release_unknown"):
            await scope.update_with_receipt(
                made.summary.id, h.actor.id, SessionPatch(title="Known storage only")
            )
        armed = False
        current = (
            await PostgresSessionStore(h.root).for_actor(h.actor).get(made.summary.id, h.actor.id)
        )
        assert current is not None
        assert current.summary.title == "Known storage only"
        assert current.summary.entry_count == 1
    finally:
        armed = False
        assert await root.aclose()


async def test_sql_receipt_bounds_refuse_before_mutation_and_leave_original_frontier(database):
    h = database
    store = PostgresSessionStore(h.root).for_actor(h.actor)
    made = await store.create_with_receipt(h.actor.id, SessionInit())
    h.root.limits = h.root.limits.model_copy(update={"max_batch_bytes": 8192})
    with pytest.raises(StoreError, match="write_bytes"):
        await store.update_with_receipt(
            made.summary.id, h.actor.id, SessionPatch(title="x" * 16384)
        )
    view = await store.get(made.summary.id, h.actor.id)
    assert view is not None
    assert view.summary.title is None
    assert view.summary.last_seq == made.summary.last_seq
