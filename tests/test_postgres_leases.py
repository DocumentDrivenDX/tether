"""Server-time generations fence actual competing database connections."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from psycopg import sql
from test_postgres_primitives import contents
from test_postgres_read_integrity import snapshot
from test_postgres_restore_primitives import pending_values

from tether.stores.postgres import DatabaseRoot
from tether.stores.postgres.changes import Changes
from tether.stores.postgres.leases import Leases
from tether.types.execution import Actor, CommitUnknown, NewEntry, WorkerOutstanding
from tether.types.storage import (
    LeaseLost,
    SessionClosed,
    SessionInit,
    StorageLimits,
    TurnInProgress,
    UnknownSession,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def expire(h, session_id):
    async with h.pool.connection() as connection:
        await connection.execute(
            sql.SQL(
                "UPDATE {}.sessions SET lease_until=clock_timestamp()-interval '1 second' "
                "WHERE owner=%s AND id=%s"
            ).format(sql.Identifier(h.schema)),
            (h.actor.id.encode(), session_id.encode()),
        )


def start(turn="phase"):
    return NewEntry(
        kind="turn_started",
        turn_id=turn,
        facts={
            "spec_id": "demo",
            "spec_version": "1",
            "spec_digest": "digest",
        },
    )


async def test_independent_roots_compete_and_stale_tokens_cannot_write_or_release(database):
    h = database
    leases = Leases(h.root, h.actor)
    summary = await leases.create(SessionInit(), str(uuid4()))
    other = await DatabaseRoot.open(h.source, actor=h.actor, schema=h.schema)
    competing = Leases(other, h.actor)
    try:
        outcomes = await asyncio.gather(
            leases.acquire(summary.id, turn_id="job-a"),
            competing.acquire(summary.id, turn_id="job-b"),
            return_exceptions=True,
        )
        winner = next(value for value in outcomes if not isinstance(value, BaseException))
        assert sum(isinstance(value, TurnInProgress) for value in outcomes) == 1
        original, _ = winner
        await leases.append_fenced(original, [start()])
        await expire(h, summary.id)
        replacement, frame = await competing.acquire(summary.id, turn_id="replacement")
        assert replacement.generation == original.generation + 1
        assert frame.projection.summary.status == "interrupted"
        before = await contents(leases, summary.id)
        with pytest.raises(LeaseLost):
            await leases.append_fenced(original, [NewEntry(kind="message")])
        with pytest.raises(LeaseLost):
            await leases.heartbeat(original)
        await leases.release(original)
        assert await contents(leases, summary.id) == before
        await competing.append_fenced(replacement, [start("replacement-phase")])
        await competing.release(replacement)
        _, frame, entries = await contents(leases, summary.id)
        assert frame.lease_turn is None
        assert entries[-1].kind == "turn_ended"
        assert entries[-1].turn_id == "replacement-phase"
        assert frame.generation == replacement.generation
    finally:
        assert await other.aclose()


async def test_confirmation_recovery_reports_logical_phase_not_new_lease_job(database):
    h = database
    leases = Leases(h.root, h.actor)
    summary = await leases.create(SessionInit(), str(uuid4()))
    original, _ = await leases.acquire(summary.id, turn_id="send-job")
    values, _ = pending_values(summary, h.actor)
    await leases.append_fenced(original, values)
    await leases.release(original)
    continuation, _ = await leases.acquire(summary.id, turn_id="confirmation-job")
    approval = values[2].model_copy(
        update={
            "key": "accepted",
            "facts": {
                **values[2].facts,
                "state": "approved",
            },
        }
    )
    await leases.append_fenced(
        continuation, [approval, NewEntry(kind="turn_resumed", turn_id="turn")]
    )
    await expire(h, summary.id)
    recovered = await leases.recover_one(summary.id, now=datetime.now(UTC))
    assert recovered is not None
    assert recovered.turn_id == "turn"
    assert recovered.resumable
    _, frame, entries = await contents(leases, summary.id)
    assert frame.lease_turn is None
    assert frame.projection.summary.pending
    assert frame.projection.summary.unresolved_effect_count == 1
    assert entries[-1].turn_id == "turn"
    assert await leases.recover_one(summary.id, now=datetime.now(UTC)) is None


async def test_future_client_recovery_clock_cannot_steal_a_live_lease(database):
    h = database
    leases = Leases(h.root, h.actor)
    summary = await leases.create(SessionInit(), str(uuid4()))
    token, frame = await leases.acquire(summary.id, turn_id="job")
    await leases.append_fenced(token, [start()])
    before = await contents(leases, summary.id)
    assert await leases.recover_one(summary.id, now=datetime.now(UTC) + timedelta(days=1)) is None
    assert await contents(leases, summary.id) == before
    await leases.heartbeat(token)
    assert (await contents(leases, summary.id))[1].lease_until > frame.lease_until
    await leases.release(token)


async def test_expiry_during_entry_apply_rolls_back_facts_projection_and_feed(
    database, monkeypatch
):
    h = database
    root = await DatabaseRoot.open(
        h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(lease_seconds=0.2)
    )
    leases = Leases(root, h.actor)
    original = leases.apply

    async def delayed(connection, frame, entry):
        await original(connection, frame, entry)
        await connection.execute("SELECT pg_sleep(0.25)")

    try:
        summary = await leases.create(SessionInit(), str(uuid4()))
        token, _ = await leases.acquire(summary.id, turn_id="job")
        before = await contents(leases, summary.id)
        monkeypatch.setattr(leases, "apply", delayed)
        with pytest.raises(LeaseLost):
            await leases.append_fenced(token, [NewEntry(kind="message")])
        assert await contents(leases, summary.id) == before
    finally:
        assert await root.aclose()


async def test_heartbeat_checks_server_clock_after_waiting_for_frame(database, monkeypatch):
    h = database
    root = await DatabaseRoot.open(
        h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(lease_seconds=0.2)
    )
    leases = Leases(root, h.actor)
    original = leases.frame

    async def delayed(connection, session_id, *, write):
        frame = await original(connection, session_id, write=write)
        await connection.execute("SELECT pg_sleep(0.25)")
        return frame

    try:
        summary = await leases.create(SessionInit(), str(uuid4()))
        token, _ = await leases.acquire(summary.id, turn_id="job")
        monkeypatch.setattr(leases, "frame", delayed)
        with pytest.raises(LeaseLost):
            await leases.heartbeat(token)
    finally:
        assert await root.aclose()


async def test_unknown_acquisition_does_not_grant_a_second_handle(database):
    h = database
    leases = Leases(h.root, h.actor)
    summary = await leases.create(SessionInit(), str(uuid4()))
    fail = False

    @asynccontextmanager
    async def source(actor, session_id):
        async with h.pool.connection() as connection:
            yield connection
        if fail:
            raise RuntimeError("fabricated_ack_loss")

    root = await DatabaseRoot.open(source, actor=h.actor, schema=h.schema)
    try:
        fail = True
        with pytest.raises(CommitUnknown):
            await Leases(root, h.actor).acquire(summary.id, turn_id="unknown")
        with pytest.raises(TurnInProgress):
            await leases.acquire(summary.id, turn_id="duplicate")
        fail = False
        await expire(h, summary.id)
        replacement, _ = await leases.acquire(summary.id, turn_id="replacement")
        assert replacement.generation == 2
        await leases.release(replacement)
    finally:
        assert await root.aclose()


async def test_unknown_append_can_be_reconciled_by_original_key_without_a_new_commit(database):
    h = database
    leases = Leases(h.root, h.actor)
    summary = await leases.create(SessionInit(), str(uuid4()))
    token, _ = await leases.acquire(summary.id, turn_id="job")
    fail = False

    @asynccontextmanager
    async def source(actor, session_id):
        async with h.pool.connection() as connection:
            yield connection
        if fail:
            raise RuntimeError("fabricated_ack_loss")

    root = await DatabaseRoot.open(source, actor=h.actor, schema=h.schema)
    try:
        values = [NewEntry(kind="message", key="original")]
        fail = True
        with pytest.raises(CommitUnknown):
            await Leases(root, h.actor).append_fenced(token, values)
        fail = False
        before = await contents(leases, summary.id)
        accepted = await leases.append_fenced(token, values)
        assert accepted == before[2]
        assert await contents(leases, summary.id) == before
        await leases.release(token)
    finally:
        assert await root.aclose()


async def test_actual_worker_retention_refuses_expired_takeover_across_roots(database):
    h = database
    leases = Leases(h.root, h.actor)
    summary = await leases.create(SessionInit(), str(uuid4()))
    token, _ = await leases.acquire(summary.id, turn_id="job")
    await leases.append_fenced(token, [start()])
    root = await DatabaseRoot.open(h.source, actor=h.actor, schema=h.schema)
    started, release = Event(), Event()

    def work():
        started.set()
        if not release.wait(5):
            raise RuntimeError("fabricated_worker_deadline")

    future = asyncio.get_running_loop().run_in_executor(None, work)
    h.root.workers.retain(summary.id, future)
    try:
        assert await asyncio.to_thread(started.wait, 3)
        await expire(h, summary.id)
        with pytest.raises(WorkerOutstanding):
            await Leases(root, h.actor).acquire(summary.id, turn_id="replacement")
        await leases.release(token)
        assert root.workers.active is h.root.workers.active
        assert root.workers.busy(summary.id)
        view = (await Changes(root, h.actor).snapshot(summary.id)).view
        assert view.summary.status == "interrupted"
        assert view.summary.active_turn_id is None
        with pytest.raises(WorkerOutstanding):
            await Leases(root, h.actor).acquire(summary.id, turn_id="replacement")
        release.set()
        await asyncio.wait_for(asyncio.shield(future), 3)
        replacement, _ = await Leases(root, h.actor).acquire(summary.id, turn_id="replacement")
        await Leases(root, h.actor).release(replacement)
    finally:
        release.set()
        await asyncio.wait_for(asyncio.shield(future), 3)
        assert await root.aclose()


async def test_closed_and_foreign_sessions_refuse_lease_acquisition(database):
    h = database
    leases = Leases(h.root, h.actor)
    summary = await leases.create(SessionInit(), str(uuid4()))
    token, _ = await leases.acquire(summary.id, turn_id="job")
    await leases.append_fenced(token, [NewEntry(kind="session", facts={"event": "closed"})])
    await leases.release(token)
    with pytest.raises(SessionClosed):
        await leases.acquire(summary.id, turn_id="closed")
    with pytest.raises(UnknownSession):
        await Leases(h.root, Actor(id="different")).acquire(summary.id, turn_id="foreign")


@pytest.mark.parametrize("stage", ["publish", "renew", "write_lease"])
async def test_expiry_at_final_mutation_cannot_commit_or_resurrect_ownership(
    database, monkeypatch, stage
):
    h = database
    root = await DatabaseRoot.open(
        h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(lease_seconds=0.2)
    )
    leases = Leases(root, h.actor)
    summary = await leases.create(SessionInit(), str(uuid4()))
    token = None
    if stage != "write_lease":
        token, _ = await leases.acquire(summary.id, turn_id="job")
    original = getattr(leases, stage)

    async def delayed(*args):
        if stage == "renew":
            await args[0].execute("SELECT pg_sleep(0.25)")
        result = await original(*args)
        if stage != "renew":
            await args[0].execute("SELECT pg_sleep(0.25)")
        return result

    before = await snapshot(h)
    monkeypatch.setattr(leases, stage, delayed)
    try:
        request = (
            leases.acquire(summary.id, turn_id="job")
            if token is None
            else leases.heartbeat(token)
            if stage == "renew"
            else leases.append_fenced(token, [NewEntry(kind="message")])
        )
        with pytest.raises(LeaseLost):
            await request
        assert await snapshot(h) == before
        monkeypatch.setattr(leases, stage, original)
        replacement, _ = await leases.acquire(summary.id, turn_id="replacement")
        assert replacement.generation == (token.generation + 1 if token is not None else 1)
        await leases.release(replacement)
    finally:
        assert await root.aclose()
