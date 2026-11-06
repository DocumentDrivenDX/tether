"""Shared-root content publication joins the existing fenced journal transaction."""

from contextlib import asynccontextmanager

import pytest
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server

from tether.stores.postgres import DatabaseRoot, PostgresHistory
from tether.stores.postgres.store import PostgresSessionStore
from tether.types import Message
from tether.types.execution import Actor, CommitUnknown, EntryConflict, ExecutionDenied, NewEntry
from tether.types.history import HistoryRecord
from tether.types.storage import SessionInit, StoreError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def record(text="Fabricated message"):
    return HistoryRecord(kind="message", message=Message(role="user", content=text))


def entry(ref, key="message"):
    return NewEntry(
        kind="message",
        key=key,
        turn_id="operation",
        history_ref=ref,
        facts={"role": "user", "is_error": False},
    )


async def test_only_accepting_batch_publishes_content_and_feed(database):
    h = database
    store = PostgresSessionStore(h.root).for_actor(h.actor)
    session = await store.create(h.actor.id, SessionInit())
    async with store.turn(session.id, h.actor.id, turn_id="operation") as log:
        history = h.history.for_turn(log)
        ref = await history.put(
            actor=h.actor, session_id=session.id, key="content", record=record()
        )
        assert (await history.read([ref], actor=h.actor))[0].status == "ready"
        assert (await h.history.read([ref], actor=h.actor))[0].status == "missing"
        await log.append([NewEntry(kind="authorization", facts={"allowed": True})])
        assert (await h.history.read([ref], actor=h.actor))[0].status == "missing"
        before = await store.watermark(h.actor.id)
        accepted = await log.append([entry(ref)])
        assert (await h.history.read([ref], actor=h.actor))[0].record == record()
        view = await store.get(session.id, h.actor.id)
        assert view is not None
        assert view.summary.message_count == 1
        assert await store.watermark(h.actor.id) == before + 1
        assert (await log.append([entry(ref)])) == accepted


async def test_journal_conflict_rolls_back_new_content(database):
    h = database
    store = PostgresSessionStore(h.root).for_actor(h.actor)
    session = await store.create(h.actor.id, SessionInit())
    async with store.turn(session.id, h.actor.id, turn_id="operation") as log:
        await log.append([NewEntry(kind="authorization", key="conflict", facts={"allowed": True})])
        history = h.history.for_turn(log)
        ref = await history.put(
            actor=h.actor, session_id=session.id, key="content", record=record()
        )
        before = await store.watermark(h.actor.id)
        with pytest.raises(EntryConflict):
            await log.append([entry(ref, "conflict")])
        assert (await h.history.read([ref], actor=h.actor))[0].status == "missing"
        assert await store.watermark(h.actor.id) == before
        view = await store.get(session.id, h.actor.id)
        assert view is not None
        assert view.summary.message_count == 0


async def test_history_conflict_rolls_back_entire_batch(database):
    h = database
    store = PostgresSessionStore(h.root).for_actor(h.actor)
    session = await store.create(h.actor.id, SessionInit())
    await h.history.put(actor=h.actor, session_id=session.id, key="content", record=record("old"))
    async with store.turn(session.id, h.actor.id, turn_id="operation") as log:
        history = h.history.for_turn(log)
        ref = await history.put(
            actor=h.actor, session_id=session.id, key="content", record=record()
        )
        with pytest.raises(EntryConflict, match="history_conflict"):
            await log.append([entry(ref)])
        assert not log.entries
        view = await store.get(session.id, h.actor.id)
        assert view is not None
        assert view.summary.message_count == 0


async def test_bound_history_refuses_foreign_actor_session_root_and_closed_lease(database):
    h = database
    store = PostgresSessionStore(h.root).for_actor(h.actor)
    session = await store.create(h.actor.id, SessionInit())
    other = await DatabaseRoot.open(h.source, actor=h.actor, schema=h.schema)
    try:
        async with store.turn(session.id, h.actor.id, turn_id="operation") as log:
            history = h.history.for_turn(log)
            with pytest.raises(ExecutionDenied, match="history_transaction_scope"):
                await history.put(
                    actor=Actor(id="foreign"), session_id=session.id, key="x", record=record()
                )
            with pytest.raises(ExecutionDenied, match="history_transaction_scope"):
                await history.put(actor=h.actor, session_id="foreign", key="x", record=record())
            with pytest.raises(StoreError, match="history_transaction_mismatch"):
                PostgresHistory(other, transaction_profile="shared").for_turn(log)
            independent = PostgresHistory(h.root, transaction_profile="independent")
            assert independent.for_turn(log) is independent
        with pytest.raises(Exception, match="lease_lost"):
            await history.put(actor=h.actor, session_id=session.id, key="x", record=record())
    finally:
        await other.aclose()


async def test_lost_acknowledgment_poison_then_reopen_proves_atomic_acceptance(database):
    h = database
    lose = False

    @asynccontextmanager
    async def source(actor, session_id):
        nonlocal lose
        async with h.pool.connection() as connection:
            yield connection
        if lose:
            lose = False
            raise RuntimeError("fabricated acknowledgment loss")

    root = await DatabaseRoot.open(source, actor=h.actor, schema=h.schema)
    store = PostgresSessionStore(root).for_actor(h.actor)
    session = await store.create(h.actor.id, SessionInit())
    try:
        async with store.turn(session.id, h.actor.id, turn_id="operation") as log:
            history = PostgresHistory(root).for_turn(log)
            ref = await history.put(
                actor=h.actor, session_id=session.id, key="content", record=record()
            )
            lose = True
            with pytest.raises(CommitUnknown):
                await log.append([entry(ref)])
            with pytest.raises(CommitUnknown):
                await history.put(
                    actor=h.actor, session_id=session.id, key="other", record=record()
                )
        async with store.turn(session.id, h.actor.id, turn_id="reopen") as log:
            assert [e.history_ref for e in log.entries if e.key == "message"] == [ref]
            assert (await h.history.read([ref], actor=h.actor))[0].record == record()
            view = await store.get(session.id, h.actor.id)
            assert view is not None
            assert view.summary.message_count == 1
    finally:
        await root.aclose()


async def test_atomic_operation_key_binds_full_batch_and_original_frontier_across_reopen(database):
    h = database
    store = PostgresSessionStore(h.root).for_actor(h.actor)
    session = await store.create(h.actor.id, SessionInit())
    async with store.turn(session.id, h.actor.id, turn_id="operation") as log:
        history = h.history.for_turn(log)
        ref = await history.put(
            actor=h.actor, session_id=session.id, key="content", record=record()
        )
        accepted = await log.append([entry(ref)], operation_key="batch", expected_frontier=0)
        receipt = next(e for e in log.entries if e.key == "history-batch:batch")
        assert receipt.facts["expected_frontier"] == 0
    watermark = await store.watermark(h.actor.id)
    async with store.turn(session.id, h.actor.id, turn_id="reopened") as log:
        assert (
            await log.append([entry(ref)], operation_key="batch", expected_frontier=0) == accepted
        )
        with pytest.raises(EntryConflict, match="history_batch_conflict"):
            await log.append([entry(ref)], operation_key="batch", expected_frontier=1)
        changed = entry(ref).model_copy(update={"facts": {"role": "assistant", "is_error": False}})
        with pytest.raises(EntryConflict, match="history_batch_conflict"):
            await log.append([changed], operation_key="batch", expected_frontier=0)
    assert await store.watermark(h.actor.id) == watermark


async def test_unkeyed_batches_bind_nested_reference_identity_without_colliding(database):
    h = database
    store = PostgresSessionStore(h.root).for_actor(h.actor)
    session = await store.create(h.actor.id, SessionInit())
    async with store.turn(session.id, h.actor.id, turn_id="operation") as log:
        history = h.history.for_turn(log)
        accepted = []
        for key in ("first", "second"):
            ref = await history.put(actor=h.actor, session_id=session.id, key=key, record=record())
            batch = [
                NewEntry(
                    kind="preparation",
                    turn_id="operation",
                    facts={"state": "ready", "references": [ref.model_dump(mode="json")]},
                )
            ]
            accepted.append(await log.append(batch))
        assert accepted[0][0].index != accepted[1][0].index
        count = len(log.entries)
        assert await log.append(batch) == accepted[1]
        assert len(log.entries) == count
    watermark = await store.watermark(h.actor.id)
    async with store.turn(session.id, h.actor.id, turn_id="reopened") as log:
        assert h.history.for_turn(log) is not h.history
        assert await log.append(batch) == accepted[1]
        assert len(log.entries) == count
    assert await store.watermark(h.actor.id) == watermark
