"""In-process JSONL/Memory change feeds wake from publication without polling."""

from __future__ import annotations

import asyncio
from typing import Any, TypeVar

import pytest

from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.stores.memory import MemoryFiles
from tether.types.execution import CommitUnknown
from tether.types.storage import ChangeGap, SessionInit, SessionPatch, StorageLimits, StoreError

pytestmark = pytest.mark.anyio
T = TypeVar("T")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(params=("memory", "jsonl"))
async def change_store(request, tmp_path):
    limits = StorageLimits(change_poll_seconds=30, feed_retention=2)
    files = (
        MemoryFiles(limits)
        if request.param == "memory"
        else await JsonlRoot.open(tmp_path, limits=limits)
    )
    store = await JsonlSessionStore.open(files)
    yield store
    assert await store.aclose()


class WakeBarrier:
    def __init__(self, store: JsonlSessionStore) -> None:
        self.entered = asyncio.Event()
        self.count = 0
        self.store = store
        self.original = store._wait_for_change

    async def wait(self, event: asyncio.Event) -> None:
        self.count += 1
        self.entered.set()
        await self.original(event)

    async def until(self, count: int, *operations: asyncio.Task[T]) -> None:
        while self.count < count:
            self.entered.clear()
            if self.count < count:
                await _wait_state_or_terminal(self.entered, *operations)


async def _wait_state_or_terminal(state: asyncio.Event, *operations: asyncio.Task[T]) -> None:
    waiting = asyncio.create_task(state.wait())
    try:
        done, _ = await asyncio.wait({waiting, *operations}, return_when=asyncio.FIRST_COMPLETED)
        observed = waiting in done
    finally:
        if not waiting.done():
            waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
    if observed:
        return
    for operation in operations:
        if operation in done:
            await operation
    raise AssertionError("operation ended before expected state")


def observe_waits(monkeypatch, store: JsonlSessionStore) -> WakeBarrier:
    barrier = WakeBarrier(store)
    monkeypatch.setattr(store, "_wait_for_change", barrier.wait)
    return barrier


async def test_commit_between_event_capture_and_wait_is_not_lost(change_store, monkeypatch):
    store = change_store
    captured = asyncio.Event()
    resume_wait = asyncio.Event()
    original = store._wait_for_change

    async def delay_after_capture(event: asyncio.Event) -> None:
        captured.set()
        await resume_wait.wait()
        await original(event)

    monkeypatch.setattr(store, "_wait_for_change", delay_after_capture)
    feed = store.changes("owner", since=0)
    pending = asyncio.create_task(anext(feed))
    try:
        await _wait_state_or_terminal(captured, pending)
        await store.create("owner", SessionInit())
        resume_wait.set()
        change = await pending
        assert change.seq == 1
        assert change.summary is not None
    finally:
        resume_wait.set()
        if not pending.done():
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            await pending
        await feed.aclose()


async def test_repeated_commits_wake_multiple_readers(change_store, monkeypatch):
    store = change_store
    session = await store.create("owner", SessionInit())
    first = store.changes("owner", session_id=session.id, since=1)
    second = store.changes("owner", session_id=session.id, since=1)
    barrier = observe_waits(monkeypatch, store)
    reads = [asyncio.create_task(anext(first)), asyncio.create_task(anext(second))]
    next_reads: list[asyncio.Task[Any]] = []
    try:
        await barrier.until(2, *reads)
        await store.update(session.id, "owner", SessionPatch(title="one"))
        updates = await asyncio.gather(*reads)
        assert [change.seq for change in updates] == [2, 2]
        assert updates[0] == updates[1]

        next_reads = [asyncio.create_task(anext(first)), asyncio.create_task(anext(second))]
        await barrier.until(4, *next_reads)
        await store.update(session.id, "owner", SessionPatch(title="two"))
        later = await asyncio.gather(*next_reads)
        assert [change.seq for change in later] == [3, 3]
        assert all(change.summary is not None and change.summary.title == "two" for change in later)
    finally:
        all_reads = [*reads, *next_reads]
        for task in all_reads:
            if not task.done():
                task.cancel()
        await asyncio.gather(*all_reads, return_exceptions=True)
        await first.aclose()
        await second.aclose()


async def test_filtered_reader_advances_over_unrelated_commit_then_wakes(change_store, monkeypatch):
    store = change_store
    selected = await store.create("owner", SessionInit())
    feed = store.changes("owner", session_id=selected.id, since=1)
    barrier = observe_waits(monkeypatch, store)
    pending = asyncio.create_task(anext(feed))
    try:
        await barrier.until(1, pending)
        await store.create("owner", SessionInit())
        await barrier.until(2, pending)
        await store.update(selected.id, "owner", SessionPatch(title="selected"))
        change = await pending
        assert change.seq == 3
        assert change.session_id == selected.id
        assert change.summary is not None
        assert change.summary.title == "selected"
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await feed.aclose()


async def test_retention_gap_is_still_refused_before_wait():
    files = MemoryFiles(StorageLimits(change_poll_seconds=30, feed_retention=2))
    store = await JsonlSessionStore.open(files)
    try:
        for _ in range(3):
            await store.create("owner", SessionInit())
        feed = store.changes("owner", since=0)
        with pytest.raises(ChangeGap, match="too_old"):
            await anext(feed)
        await feed.aclose()
    finally:
        assert await store.aclose()


async def test_unknown_commit_wakes_reader_and_refuses_stale_projection(change_store, monkeypatch):
    store = change_store
    barrier = observe_waits(monkeypatch, store)
    feed = store.changes("owner", since=0)
    pending = asyncio.create_task(anext(feed))
    await barrier.until(1, pending)

    async def unknown_append(_name, _value, *, guard=None):
        raise CommitUnknown("fabricated_unknown")

    monkeypatch.setattr(store.files, "append_file", unknown_append)
    with pytest.raises(CommitUnknown):
        await store.create("owner", SessionInit())
    with pytest.raises(StoreError, match="store_requires_reopen"):
        await pending
    await feed.aclose()
    assert store._failed


async def test_prewrite_store_refusal_keeps_store_and_waiter_usable(monkeypatch):
    store = await JsonlSessionStore.open(MemoryFiles(StorageLimits(change_poll_seconds=30)))
    barrier = observe_waits(monkeypatch, store)
    feed = store.changes("owner", since=0)
    pending = asyncio.create_task(anext(feed))
    try:
        await barrier.until(1, pending)
        original = store.files.append_file

        async def refuse(name, value, *, guard=None):
            raise StoreError("writer_unavailable")

        monkeypatch.setattr(store.files, "append_file", refuse)
        with pytest.raises(StoreError, match="writer_unavailable"):
            await store.create("owner", SessionInit())
        monkeypatch.setattr(store.files, "append_file", original)
        assert not store._failed
        assert not pending.done()

        await store.create("owner", SessionInit())
        change = await pending
        assert change.seq == 1
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await feed.aclose()
        assert await store.aclose()


async def test_close_wakes_blocked_reader_and_cancellation_does_not_poison(
    change_store, monkeypatch
):
    store = change_store
    barrier = observe_waits(monkeypatch, store)
    feed = store.changes("owner", since=0)
    other_feed = store.changes("owner", since=0)
    pending = asyncio.create_task(anext(feed))
    other_pending = asyncio.create_task(anext(other_feed))
    await barrier.until(2, pending, other_pending)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    await feed.aclose()
    assert not store._failed

    await store.create("owner", SessionInit())
    received = await other_pending
    assert received.seq == 1
    await other_feed.aclose()

    second = store.changes("owner", since=1)
    closing = asyncio.create_task(anext(second))
    await barrier.until(3, closing)
    assert await store.aclose()
    with pytest.raises(StoreError, match="store_requires_reopen"):
        await closing
    await second.aclose()
