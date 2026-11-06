"""Persisted Client replay and independent bounded follower lifecycle."""

from __future__ import annotations

import asyncio
from contextlib import aclosing

import pytest
from selected_profiles_process import await_retained
from test_client_reads import harness as harness

from tether.types.client import EntriesAppended, Lagged, ReadLimit, ResyncRequired, SessionUpdated
from tether.types.execution import ExecutionDenied
from tether.types.storage import SessionPatch

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _event_or_operation_terminal(event: asyncio.Event, operation: asyncio.Task):
    ready = asyncio.create_task(event.wait())
    try:
        done, _ = await asyncio.wait({ready, operation}, return_when=asyncio.FIRST_COMPLETED)
        if operation in done and ready not in done:
            await operation
            raise AssertionError("operation terminated before event witness")
        await ready
    finally:
        if not ready.done():
            ready.cancel()
        await _settle_tasks(ready)


async def _settle_tasks(*tasks: asyncio.Future):
    async def settle() -> None:
        await asyncio.gather(*tasks, return_exceptions=True)

    await await_retained(asyncio.create_task(settle()))


async def test_snapshot_replay_variants_keep_one_final_marker(harness):
    h, sid = harness, harness.session.id
    snapshot = await h.client.snapshot(sid)
    assert await h.store.watermark(h.actor.id) == snapshot.seq
    await h.store.update(sid, h.actor.id, SessionPatch(title="Fabricated new title"))
    async with aclosing(h.client.follow(session_id=sid, since=snapshot.seq)) as stream:
        first = await anext(stream)
        second = await anext(stream)
        assert isinstance(first, SessionUpdated)
        assert not first.last_in_commit
        assert isinstance(second, EntriesAppended)
        assert second.last_in_commit
        assert first.seq == second.seq > snapshot.seq
        assert second.entries[0].facts["value"] == "Fabricated new title"
    assert not h.app.followers.tasks


async def test_each_variant_rechecks_current_read_policy(harness):
    h, sid = harness, harness.session.id
    snapshot = await h.client.snapshot(sid)
    await h.store.update(sid, h.actor.id, SessionPatch(title="Fabricated"))
    async with aclosing(h.client.follow(session_id=sid, since=snapshot.seq)) as stream:
        assert isinstance(await anext(stream), SessionUpdated)
        h.policy.read = False
        with pytest.raises(ExecutionDenied):
            await anext(stream)
    assert not h.app.followers.tasks


async def test_slow_replay_stays_bounded_without_losing_retained_commits(harness):
    h, sid = harness, harness.session.id
    h.app.access.limits = h.app.access.limits.model_copy(update={"follower_queue_size": 1})
    snapshot = await h.client.snapshot(sid)
    for index in range(4):
        await h.store.update(sid, h.actor.id, SessionPatch(title=f"Fabricated {index}"))
    last = await h.store.watermark(h.actor.id)
    closed, lagged = [], False
    async with aclosing(h.client.follow(session_id=sid, since=snapshot.seq)) as stream:
        while not closed or closed[-1] < last:
            value = await anext(stream)
            if isinstance(value, Lagged):
                assert value.missed == 0
                lagged = True
            elif value.last_in_commit:
                closed.append(value.seq)
    assert closed == list(range(snapshot.seq + 1, last + 1))
    assert lagged


async def test_retention_gap_resets_then_continues_from_captured_watermark(harness):
    h, sid = harness, harness.session.id
    h.store.files.limits = h.store.files.limits.model_copy(update={"feed_retention": 1})
    await h.store.update(sid, h.actor.id, SessionPatch(title="Fabricated retained"))
    async with aclosing(h.client.follow(session_id=sid, since=0)) as stream:
        value = await anext(stream)
        assert isinstance(value, ResyncRequired)
        assert value.reason == "too_old"
        baseline = await h.client.snapshot(sid)
        await h.store.update(sid, h.actor.id, SessionPatch(title="Fabricated after reset"))
        value = await anext(stream)
        assert isinstance(value, SessionUpdated)
        assert value.seq > baseline.seq


async def test_close_wakes_idle_follower_and_follower_limits_are_finite(harness, monkeypatch):
    h, sid = harness, harness.session.id
    h.app.access.limits = h.app.access.limits.model_copy(update={"max_followers": 1})
    ready = asyncio.Event()
    original = h.store.watermark

    async def mark(owner):
        result = await original(owner)
        ready.set()
        return result

    monkeypatch.setattr(h.store, "watermark", mark)
    stream = h.client.follow(session_id=sid)
    waiting = asyncio.create_task(anext(stream))
    try:
        await _event_or_operation_terminal(ready, waiting)
        async with aclosing(h.client.follow(session_id=sid)) as other:
            with pytest.raises(ReadLimit, match="follower_limit"):
                await anext(other)
        assert await h.app.aclose()
        with pytest.raises(RuntimeError, match="follower_closed"):
            await waiting
    finally:
        waiting.cancel()
        await _settle_tasks(waiting)
        await stream.aclose()
    assert not h.app.followers.tasks


@pytest.mark.parametrize("shutdown", ["cancel", "timeout"])
async def test_cleanup_seals_engine_even_if_follower_close_is_interrupted(
    harness, monkeypatch, shutdown
):
    from tether.types.storage import StoreError

    h = harness
    entered, release = asyncio.Event(), asyncio.Event()
    original = h.app.followers.close

    async def blocked():
        entered.set()
        await release.wait()
        await original()

    monkeypatch.setattr(h.app.followers, "close", blocked)
    if shutdown == "timeout":
        h.app.limits = h.app.limits.model_copy(update={"shutdown_timeout_seconds": 0.01})
    closing = asyncio.create_task(h.app.aclose())
    try:
        await _event_or_operation_terminal(entered, closing)
        assert h.engine.workers.closed
        if shutdown == "cancel":
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
        else:
            assert await closing is False
        assert not h.app.drained
    finally:
        release.set()
        await _settle_tasks(closing)
        if h.app._cleanup is not None:
            await asyncio.shield(h.app._cleanup)
    assert h.app.drained
    with pytest.raises(StoreError):
        await h.store.get(h.session.id, h.actor.id)
