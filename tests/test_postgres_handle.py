"""A known prefix cannot grant dispatch after uncertainty, expiry or concurrent close."""

import asyncio
import sys
from threading import Event
from typing import Any
from uuid import uuid4

import pytest
from postgres_pending_io_controls import (
    PendingIOControl,
    close_root_and_drain,
    gate_admitted_operation,
    retain_cleanup,
    wait_event_or_task,
)
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from resource_lifetime_controls import cancel_and_expect_cancellation
from test_postgres_leases import expire
from test_postgres_read_integrity import snapshot

from tether.stores.postgres import DatabaseRoot
from tether.stores.postgres.capture import Capture
from tether.stores.postgres.changes import Changes
from tether.stores.postgres.handle import TurnHandle
from tether.stores.postgres.store import PostgresSessionStore
from tether.types.execution import CommitUnknown, EntryConflict, NewEntry, WorkerOutstanding
from tether.types.storage import LeaseLost, SessionInit, StorageLimits, StoreError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def handle(h, root=None):
    capture = Capture(root or h.root, h.actor)
    summary = await capture.create(SessionInit(), str(uuid4()))
    token, _ = await capture.acquire(summary.id, turn_id="job")
    index, prefix = await capture.load(token)
    return TurnHandle(capture, token, index, prefix), token


async def hold_staged_append(h, root, loop, entered, release, monkeypatch, owners: dict[str, Any]):
    capture = Capture(root, h.actor)
    summary = await capture.create(SessionInit(), str(uuid4()))
    owners["capture"] = capture
    token, _ = await capture.acquire(summary.id, turn_id="job")
    owners["token"] = token
    index, prefix = await capture.load(token)
    log = TurnHandle(capture, token, index, prefix)
    owners["log"] = log
    await log.append(
        [NewEntry(kind="message", key=f"prefix:{i}", turn_id="prefix") for i in range(100)]
    )
    prefix = log.entries
    original = log._stage
    retain = root.workers.retain

    def retain_worker(session_id, worker):
        owners["actual"] = worker
        return retain(session_id, worker)

    monkeypatch.setattr(root.workers, "retain", retain_worker)

    def block(result):
        loop.call_soon_threadsafe(entered.set)
        release.wait()
        return original(result)

    monkeypatch.setattr(log, "_stage", block)
    task = asyncio.create_task(log.append([NewEntry(kind="message", key="accepted")]))
    owners["task"] = task
    owners["prefix"] = prefix
    await wait_event_or_task(entered, task)


async def _settle_staged_owners(owners):
    errors = []
    task = owners.get("task")
    actual = owners.get("actual")
    if task is not None and not task.done():
        task.cancel()
    settled = await asyncio.gather(
        *(owner for owner in (task, actual) if owner is not None), return_exceptions=True
    )
    errors.extend(
        result
        for result in settled
        if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError)
    )
    return errors


async def _close_staged_log(owners, log_closed):
    errors = []
    log = owners.get("log")
    if not log_closed:
        try:
            if log is not None:
                await log.aclose()
            elif "token" in owners:
                await owners["capture"].release(owners["token"])
        except BaseException as error:
            errors.append(error)
            if log is not None:
                try:
                    await owners["capture"].release(owners["token"])
                except BaseException as release_error:
                    errors.append(release_error)
    return errors


async def drain_staged_root(root, release, owners, log_closed):
    release.set()
    errors = await _settle_staged_owners(owners)
    errors.extend(await _close_staged_log(owners, log_closed))
    try:
        await root.aclose()
    except BaseException as error:
        errors.append(error)
    try:
        await root.wait_closed()
    except BaseException as error:
        errors.append(error)
    if errors:
        raise BaseExceptionGroup("staged_root_cleanup_failed", errors)


async def test_prefix_copies_retries_and_known_rejections_remain_usable(database):
    log, token = await handle(database)
    try:
        entry = NewEntry(kind="message", key="first", facts={"value": [1]})
        accepted = await log.append([entry])
        accepted[0].facts["value"] = [2]
        copied = log.entries
        copied[0].facts["value"] = [3]
        assert log.entries[0].facts == {"value": [1]}
        before = log._prefix
        assert await log.append([entry]) == log.entries
        assert log._prefix == before
        with pytest.raises(EntryConflict):
            await log.append([NewEntry(kind="message", key="first", facts={"value": [2]})])
        await log.append([NewEntry(kind="message", key="next")])
        assert [e.key for e in log.entries] == ["first", "next"]
        assert log.turn_id == token.turn_id
    finally:
        await log.aclose()
    with pytest.raises(LeaseLost):
        await log.append([])


async def test_expired_handle_is_terminal_and_stale_cleanup_is_harmless(database):
    h = database
    log, token = await handle(h)
    await expire(h, token.session_id)
    with pytest.raises(LeaseLost):
        await log.append([NewEntry(kind="message")])
    capture = Capture(h.root, h.actor)
    replacement, _ = await capture.acquire(token.session_id, turn_id="replacement")
    before = await snapshot(h)
    await log.aclose()
    assert await snapshot(h) == before
    with pytest.raises(LeaseLost):
        await log.heartbeat()
    await capture.release(replacement)


async def test_uncertain_commit_seals_cache_until_fresh_reconstruction(database, monkeypatch):
    h = database
    log, token = await handle(h)
    original = log._store.commit

    async def uncertain(*args, **kwargs):
        await original(*args, **kwargs)
        raise CommitUnknown("fabricated_ack_loss")

    monkeypatch.setattr(log._store, "commit", uncertain)
    with pytest.raises(CommitUnknown, match="fabricated_ack_loss"):
        await log.append([NewEntry(kind="message", key="accepted")])
    assert log.entries == ()
    calls = len(h.calls)
    with pytest.raises(CommitUnknown, match="turn_requires_reopen"):
        await log.heartbeat()
    assert len(h.calls) == calls
    rebuilt = await Capture(h.root, h.actor).reconstruct(token)
    assert [entry.key for entry in rebuilt.entries] == ["accepted"]
    await log.aclose()


async def test_failed_postcommit_staging_poison_does_not_publish_cache(database, monkeypatch):
    log, token = await handle(database)

    def fail(result):
        raise RuntimeError("fabricated_cache_failure")

    monkeypatch.setattr(log, "_stage", fail)
    with pytest.raises(CommitUnknown, match="turn_projection_unknown"):
        await log.append([NewEntry(kind="message", key="accepted")])
    assert log.entries == ()
    with pytest.raises(CommitUnknown):
        await log.append([])
    assert (await Capture(database.root, database.actor).reconstruct(token)).entries
    await log.aclose()


async def test_cancelled_staging_retains_actual_worker_and_cannot_publish(database, monkeypatch):
    h = database
    root = await DatabaseRoot.open(
        h.source, actor=h.actor, schema=h.schema, limits=StorageLimits(shutdown_seconds=0.02)
    )
    owners = {}
    log_closed = False
    entered, release = asyncio.Event(), Event()
    loop = asyncio.get_running_loop()
    try:
        await hold_staged_append(h, root, loop, entered, release, monkeypatch, owners)
        log, token = owners["log"], owners["token"]
        task, actual = owners["task"], owners["actual"]
        original_prefix = owners["prefix"]
        assert not actual.done()
        await log.heartbeat()
        await cancel_and_expect_cancellation(task)
        assert log.entries == original_prefix
        assert root.workers.busy(token.session_id)
        await log.aclose()
        log_closed = True
        assert not await root.aclose()
        with pytest.raises(WorkerOutstanding):
            await Capture(h.root, h.actor).acquire(token.session_id, turn_id="replacement")
        release.set()
        await asyncio.shield(actual)
    finally:
        await retain_cleanup(
            drain_staged_root(root, release, owners, log_closed),
            primary=sys.exception(),
        )
    log, token = owners["log"], owners["token"]
    assert log.entries == original_prefix
    replacement, _ = await Capture(h.root, h.actor).acquire(token.session_id, turn_id="replacement")
    await Capture(h.root, h.actor).release(replacement)


async def test_staging_owner_is_published_before_entry_wait_can_be_cancelled(database, monkeypatch):
    root = await DatabaseRoot.open(
        database.source,
        actor=database.actor,
        schema=database.schema,
        limits=StorageLimits(shutdown_seconds=0.02),
    )
    owners = {}
    entered, release, entry_observed = asyncio.Event(), Event(), asyncio.Event()
    loop = asyncio.get_running_loop()
    helper_task = None
    original_wait = wait_event_or_task

    async def pause_after_entry(event, task):
        await original_wait(event, task)
        entry_observed.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(sys.modules[__name__], "wait_event_or_task", pause_after_entry)
    try:
        helper_task = asyncio.create_task(
            hold_staged_append(database, root, loop, entered, release, monkeypatch, owners)
        )
        await original_wait(entry_observed, helper_task)
        assert {"capture", "token", "log", "task", "actual", "prefix"} <= owners.keys()
        assert not owners["task"].done()
        assert not owners["actual"].done()
        await cancel_and_expect_cancellation(helper_task)
    finally:

        async def teardown():
            release.set()
            if helper_task is not None and not helper_task.done():
                helper_task.cancel()
            await asyncio.gather(
                *((helper_task,) if helper_task is not None else ()),
                return_exceptions=True,
            )
            await drain_staged_root(root, release, owners, False)

        await retain_cleanup(teardown(), primary=sys.exception())


async def test_concurrent_close_stops_append_permission_after_known_commit(database, monkeypatch):
    log, token = await handle(database)
    entered, release = asyncio.Event(), asyncio.Event()
    task = None
    log_closed = False
    try:
        original = log._store.commit

        async def block(*args, **kwargs):
            result = await original(*args, **kwargs)
            entered.set()
            await release.wait()
            return result

        monkeypatch.setattr(log._store, "commit", block)
        task = asyncio.create_task(log.append([NewEntry(kind="message", key="accepted")]))
        await wait_event_or_task(entered, task)
        await log.aclose()
        log_closed = True
        release.set()
        with pytest.raises(LeaseLost):
            await task
        assert log.entries == ()
        assert (await Changes(database.root, database.actor).get(token.session_id)) is not None
    finally:

        async def teardown():
            release.set()
            if task is not None and not task.done():
                task.cancel()
            await asyncio.gather(*((task,) if task is not None else ()), return_exceptions=True)
            if not log_closed:
                await log.aclose()

        await retain_cleanup(teardown(), primary=sys.exception())


@pytest.mark.parametrize("bound", ["bytes", "count"])
async def test_cumulative_growth_refuses_before_commit_and_retries_cost_zero(database, bound):
    h = database
    limits = StorageLimits(
        max_index_bytes=2500 if bound == "bytes" else 1048576,
        page_limit=100 if bound == "bytes" else 2,
        max_startup_batches=1,
    )
    root = await DatabaseRoot.open(h.source, actor=h.actor, schema=h.schema, limits=limits)
    log, _ = await handle(h, root)
    try:
        for i in range(2):
            value = NewEntry(kind="message", key=str(i), facts={"value": "x" * 250})
            await log.append([value])
            prefix = log._prefix
            await log.append([value])
            assert log._prefix == prefix
        before = await snapshot(h)
        with pytest.raises(StoreError, match="turn_snapshot_"):
            await log.append([NewEntry(kind="message", facts={"value": "x" * 1000})])
        assert await snapshot(h) == before
        assert len(log.entries) == 2
    finally:
        await log.aclose()
        assert await root.aclose()


async def test_turn_cleanup_preserves_primary_failure_without_service_fallback(
    database, monkeypatch
):
    h = database
    store = PostgresSessionStore(h.root).for_actor(h.actor)
    session = await store.create(h.actor.id, SessionInit())

    async def refused(self, token):
        raise RuntimeError("fabricated_cleanup_refusal")

    monkeypatch.setattr(Capture, "release", refused)
    with pytest.raises(ValueError, match="primary"):
        async with store.turn(session.id, h.actor.id, turn_id="job"):
            raise ValueError("primary")
    assert all(actor.id == h.actor.id for actor, _ in h.calls)


@pytest.mark.parametrize("bound", ["bytes", "frame"])
async def test_long_holder_is_charged_after_publication_before_commit(database, bound):
    h = database
    store = Capture(h.root, h.actor)
    session = await store.create(SessionInit(), str(uuid4()))
    token, _ = await store.acquire(session.id, turn_id="holder:" + "x" * 4000)
    captured = await store.capture(token)
    limits = StorageLimits(
        **{
            (
                "max_index_bytes" if bound == "bytes" else "max_batch_bytes"
            ): captured.frame.bytes_used + 20
        }
    )
    root = await DatabaseRoot.open(h.source, actor=h.actor, schema=h.schema, limits=limits)
    limited = Capture(root, h.actor)
    index, prefix = await limited.load(token)
    log = TurnHandle(limited, token, index, prefix)
    before = await snapshot(h)
    entry = (
        NewEntry(kind="message")
        if bound == "bytes"
        else NewEntry(kind="session", facts={"event": "titled", "value": "y" * 250})
    )
    try:
        with pytest.raises(StoreError, match=r"turn_snapshot_bytes|journal_write_bytes"):
            await log.append([entry])
        assert await snapshot(h) == before
        assert log.entries == ()
    finally:
        await log.aclose()
        assert await root.aclose()


async def test_root_close_seals_its_execution_scope_without_sealing_sibling(database):
    h = database
    from tether.engine.workers import Workers
    from tether.types.execution import ExecutionDenied

    root = await DatabaseRoot.open(h.source, actor=h.actor, schema=h.schema)
    store = PostgresSessionStore(root).for_actor(h.actor)
    session = await store.create(h.actor.id, SessionInit())
    workers = Workers(1)
    async with store.turn(session.id, h.actor.id, turn_id="job") as log:
        workers.scopes[session.id] = log.workers
        assert workers.scopes[session.id].active is h.root.workers.active
    assert await root.aclose()
    observed = []
    with pytest.raises(ExecutionDenied, match="engine_closed"):
        await workers.call(session.id, lambda: observed.append("closed-root"))
    assert observed == []
    workers.scopes[session.id] = h.root.workers
    await workers.call(session.id, lambda: observed.append("sibling"))
    assert observed == ["sibling"]
    assert await workers.close(1)


async def test_admitted_preparation_is_not_submitted_after_root_gate_closes(database, monkeypatch):
    h = database
    root = await DatabaseRoot.open(h.source, actor=h.actor, schema=h.schema)
    observed = []
    admitted, release, close_entry = asyncio.Event(), asyncio.Event(), asyncio.Event()
    task = None
    closing = None
    try:
        control = PendingIOControl()
        control.install(monkeypatch)
        control.observe_wait(close_entry)
        gate_admitted_operation(root, monkeypatch, admitted, release)
        task = asyncio.create_task(root.prepare("session", lambda: observed.append("executed")))
        await wait_event_or_task(admitted, task)
        assert len(root._io.active) == 1
        assert observed == []
        closing = asyncio.create_task(root.aclose())
        await wait_event_or_task(close_entry, closing)
        assert root._io.closed
        assert root.workers.closed
        assert not task.done()
        release.set()
        assert await closing
        with pytest.raises(StoreError, match="database_root_closed"):
            await task
        assert observed == []
        assert not root.workers.active
    finally:

        async def teardown():
            release.set()
            for owner in (task, closing):
                if owner is not None and not owner.done():
                    owner.cancel()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            if closing is not None:
                await asyncio.gather(closing, return_exceptions=True)
            await close_root_and_drain(root)

        await retain_cleanup(teardown(), primary=sys.exception())


async def test_final_heartbeat_failure_after_commit_is_unknown_and_seals_handle(
    database, monkeypatch
):
    h = database
    log, token = await handle(h)

    async def refuse(token):
        raise StoreError("fabricated_capacity")

    monkeypatch.setattr(log._store, "heartbeat", refuse)
    with pytest.raises(CommitUnknown, match="turn_projection_unknown"):
        await log.append([NewEntry(kind="message", key="accepted-before-heartbeat")])
    assert log.entries == ()
    before = len(h.calls)
    with pytest.raises(CommitUnknown, match="turn_requires_reopen"):
        await log.append([])
    with pytest.raises(CommitUnknown, match="turn_requires_reopen"):
        await log.heartbeat()
    assert len(h.calls) == before
    rebuilt = await Capture(h.root, h.actor).reconstruct(token)
    assert [entry.key for entry in rebuilt.entries] == ["accepted-before-heartbeat"]
    await log.aclose()
