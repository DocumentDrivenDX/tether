"""Server-time generations fence actual competing database connections."""

import asyncio
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
from context_lifetime_controls import settle_tasks, wait_event_or_task
from postgres_pending_io_controls import (
    close_root_and_drain,
    close_root_settle_and_finalize,
    release_settle_future_and_close_root,
    retain_cleanup,
)
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


class LeaseClockControl:
    def __init__(self) -> None:
        self.sample = None
        self.expired = False
        self.deadline = None
        self.post_hook_samples = []

    def install(self, leases, monkeypatch) -> None:
        original = leases.clock

        async def controlled(connection):
            if self.expired:
                sample = await original(connection)
                self.post_hook_samples.append(sample)
                return sample
            if self.sample is None:
                self.sample = await original(connection)
            return self.sample

        monkeypatch.setattr(leases, "clock", controlled)

    async def reach_expiry(self, connection, deadline: datetime, entered: asyncio.Event) -> None:
        self.deadline = deadline
        entered.set()
        while True:
            cursor = await connection.execute("SELECT clock_timestamp()")
            row = await cursor.fetchone()
            assert row is not None
            assert isinstance(row[0], datetime)
            if row[0] > deadline:
                self.expired = True
                return


def assert_expiry_was_observed(control: LeaseClockControl, stage: str) -> None:
    assert control.expired
    if stage == "renew":
        assert control.post_hook_samples == []
    else:
        assert control.deadline is not None
        assert control.post_hook_samples
        assert all(sample > control.deadline for sample in control.post_hook_samples)


async def test_root_cleanup_waits_for_physical_drain_after_initial_close_false():
    entered, release = asyncio.Event(), asyncio.Event()

    class HeldRoot:
        async def aclose(self) -> bool:
            return False

        async def wait_closed(self) -> bool:
            entered.set()
            await release.wait()
            return True

    cleanup = None
    try:
        cleanup = asyncio.create_task(close_root_and_drain(HeldRoot()))
        await wait_event_or_task(entered, cleanup)
        assert not cleanup.done()
        release.set()
        assert await cleanup is False
    finally:
        release.set()
        if cleanup is not None:
            await settle_tasks((cleanup,))


async def test_independent_roots_compete_and_stale_tokens_cannot_write_or_release(database):
    h = database
    leases = Leases(h.root, h.actor)
    summary = await leases.create(SessionInit(), str(uuid4()))
    other = None
    try:
        other = await DatabaseRoot.open(h.source, actor=h.actor, schema=h.schema)
        competing = Leases(other, h.actor)
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
        if other is not None:
            await retain_cleanup(close_root_and_drain(other), primary=sys.exception())


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
    root = None
    token = None
    operation = None
    try:
        root = await DatabaseRoot.open(
            h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(lease_seconds=0.2)
        )
        leases = Leases(root, h.actor)
        control = LeaseClockControl()
        control.install(leases, monkeypatch)
        summary = await leases.create(SessionInit(), str(uuid4()))
        token, _ = await leases.acquire(summary.id, turn_id="job")
        before = await contents(leases, summary.id)
        original = leases.apply
        entered = asyncio.Event()

        async def delayed(connection, frame, entry):
            result = await original(connection, frame, entry)
            deadline = frame.lease_until
            assert deadline is not None
            await control.reach_expiry(connection, deadline, entered)
            return result

        monkeypatch.setattr(leases, "apply", delayed)
        operation = asyncio.create_task(leases.append_fenced(token, [NewEntry(kind="message")]))
        await wait_event_or_task(entered, operation)
        with pytest.raises(LeaseLost):
            await operation
        assert_expiry_was_observed(control, "apply")
        assert await contents(leases, summary.id) == before
    finally:
        if root is not None:
            finalize = None if token is None else lambda: Leases(h.root, h.actor).release(token)
            await retain_cleanup(
                close_root_settle_and_finalize(root, (operation,), finalize),
                primary=sys.exception(),
            )


async def test_heartbeat_checks_server_clock_after_waiting_for_frame(database, monkeypatch):
    h = database
    root = None
    token = None
    heartbeat = None
    try:
        root = await DatabaseRoot.open(
            h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(lease_seconds=0.2)
        )
        leases = Leases(root, h.actor)
        control = LeaseClockControl()
        control.install(leases, monkeypatch)
        summary = await leases.create(SessionInit(), str(uuid4()))
        token, _ = await leases.acquire(summary.id, turn_id="job")
        original = leases.frame
        entered = asyncio.Event()

        async def delayed(connection, session_id, *, write):
            frame = await original(connection, session_id, write=write)
            deadline = frame.lease_until
            assert deadline is not None
            await control.reach_expiry(connection, deadline, entered)
            return frame

        monkeypatch.setattr(leases, "frame", delayed)
        heartbeat = asyncio.create_task(leases.heartbeat(token))
        await wait_event_or_task(entered, heartbeat)
        with pytest.raises(LeaseLost):
            await heartbeat
        assert_expiry_was_observed(control, "frame")
    finally:
        if root is not None:
            finalize = None if token is None else lambda: Leases(h.root, h.actor).release(token)
            await retain_cleanup(
                close_root_settle_and_finalize(root, (heartbeat,), finalize),
                primary=sys.exception(),
            )


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

    root = None
    try:
        root = await DatabaseRoot.open(source, actor=h.actor, schema=h.schema)
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
        if root is not None:
            await retain_cleanup(close_root_and_drain(root), primary=sys.exception())


async def test_unknown_append_can_be_reconciled_by_original_key_without_a_new_commit(database):
    h = database
    leases = Leases(h.root, h.actor)
    summary = await leases.create(SessionInit(), str(uuid4()))
    token = None
    fail = False

    @asynccontextmanager
    async def source(actor, session_id):
        async with h.pool.connection() as connection:
            yield connection
        if fail:
            raise RuntimeError("fabricated_ack_loss")

    root = None
    try:
        root = await DatabaseRoot.open(source, actor=h.actor, schema=h.schema)
        token, _ = await leases.acquire(summary.id, turn_id="job")
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
        if root is not None:
            finalize = None if token is None else lambda: leases.release(token)
            await retain_cleanup(
                close_root_settle_and_finalize(root, (), finalize),
                primary=sys.exception(),
            )


async def test_actual_worker_retention_refuses_expired_takeover_across_roots(database):
    h = database
    leases = Leases(h.root, h.actor)
    summary = await leases.create(SessionInit(), str(uuid4()))
    token = None
    root = None
    future = None
    token_released = False
    started, release = asyncio.Event(), Event()
    loop = asyncio.get_running_loop()

    def work():
        loop.call_soon_threadsafe(started.set)
        release.wait()

    try:
        token, _ = await leases.acquire(summary.id, turn_id="job")
        await leases.append_fenced(token, [start()])
        root = await DatabaseRoot.open(h.source, actor=h.actor, schema=h.schema)
        future = loop.run_in_executor(None, work)
        h.root.workers.retain(summary.id, future)
        await wait_event_or_task(started, future)
        await expire(h, summary.id)
        with pytest.raises(WorkerOutstanding):
            await Leases(root, h.actor).acquire(summary.id, turn_id="replacement")
        await leases.release(token)
        token_released = True
        assert root.workers.active is h.root.workers.active
        assert root.workers.busy(summary.id)
        view = (await Changes(root, h.actor).snapshot(summary.id)).view
        assert view.summary.status == "interrupted"
        assert view.summary.active_turn_id is None
        with pytest.raises(WorkerOutstanding):
            await Leases(root, h.actor).acquire(summary.id, turn_id="replacement")
        release.set()
        await asyncio.shield(future)
        replacement, _ = await Leases(root, h.actor).acquire(summary.id, turn_id="replacement")
        await Leases(root, h.actor).release(replacement)
    finally:
        finalize = None if token is None or token_released else lambda: leases.release(token)
        await retain_cleanup(
            release_settle_future_and_close_root(root, future, release.set, finalize),
            primary=sys.exception(),
        )


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
    root = None
    token = None
    request: asyncio.Task | None = None
    try:
        root = await DatabaseRoot.open(
            h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(lease_seconds=0.2)
        )
        leases = Leases(root, h.actor)
        control = LeaseClockControl()
        control.install(leases, monkeypatch)
        summary = await leases.create(SessionInit(), str(uuid4()))
        if stage != "write_lease":
            token, _ = await leases.acquire(summary.id, turn_id="job")
        original = getattr(leases, stage)
        entered = asyncio.Event()

        async def delayed(*args):
            connection, frame = args[0], args[1]
            if stage == "renew":
                deadline = frame.lease_until
                assert deadline is not None
                await control.reach_expiry(connection, deadline, entered)
                return await original(*args)
            result = await original(*args)
            deadline = frame.lease_until
            assert deadline is not None
            await control.reach_expiry(connection, deadline, entered)
            return result

        before = await snapshot(h)
        monkeypatch.setattr(leases, stage, delayed)
        request = asyncio.create_task(
            leases.acquire(summary.id, turn_id="job")
            if token is None
            else leases.heartbeat(token)
            if stage == "renew"
            else leases.append_fenced(token, [NewEntry(kind="message")])
        )
        await wait_event_or_task(entered, request)
        with pytest.raises(LeaseLost):
            await request
        assert_expiry_was_observed(control, stage)
        assert await snapshot(h) == before
        replacement, _ = await Leases(h.root, h.actor).acquire(summary.id, turn_id="replacement")
        assert replacement.generation == (token.generation + 1 if token is not None else 1)
        await Leases(h.root, h.actor).release(replacement)
    finally:
        if root is not None:
            finalize = None if token is None else lambda: Leases(h.root, h.actor).release(token)
            await retain_cleanup(
                close_root_settle_and_finalize(root, (request,), finalize),
                primary=sys.exception(),
            )
