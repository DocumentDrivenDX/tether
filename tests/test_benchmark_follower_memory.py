from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import benchmark_follower_memory as benchmark
import benchmark_follower_memory_fixture as fixture_module
import benchmark_follower_memory_ports as ports_module
import benchmark_follower_memory_trial as benchmark_trial
import pytest
import selected_profiles_process
from benchmark_follower_memory_cleanup import close_all_retained
from benchmark_follower_memory_fixture import _summary
from context_lifetime_controls import CancellationObserver, settle_tasks, wait_event_or_task

from tether.client import EntriesAppended, Lagged, ReplyText, SessionUpdated
from tether.stores.jsonl.store import JsonlSessionStore
from tether.stores.memory import MemoryFiles
from tether.types.execution import Entry
from tether.types.storage import SessionSummary


def test_follower_reference_workload_declares_its_fixed_fixture_without_running_it() -> None:
    assert ports_module._QUEUE_SIZE == 32
    assert ports_module._HELD_TURNS == 32
    assert ports_module._TEXT_BYTES == 760
    assert ports_module._COALESCED_TEXT_BYTES == 32_768
    assert benchmark._SAMPLE_TRIALS == 100


def test_coalesced_tail_drain_accepts_immediate_live_event() -> None:
    tail = _tail_event()

    async def events():
        yield tail

    assert (
        asyncio.run(
            fixture_module._read_coalesced_tail(
                events(), "fabricated-owner", "fabricated-session", _first_event()
            )
        )
        == tail
    )


def test_coalesced_tail_drain_accepts_paired_preterminal_authorization() -> None:
    first = _first_event()
    summary = _running_summary()
    now = datetime.now(UTC)
    update = SessionUpdated(seq=10, summary=summary, last_in_commit=False)
    authorization = EntriesAppended(
        seq=10,
        session_id=summary.id,
        entries=(
            Entry(
                index=0,
                at=now,
                kind="authorization",
                turn_id=first.turn_id,
                facts={"actor_id": summary.owner},
            ),
        ),
        last_in_commit=True,
    )
    tail = _tail_event()

    async def events():
        yield update
        yield authorization
        yield tail

    assert (
        asyncio.run(fixture_module._read_coalesced_tail(events(), summary.owner, summary.id, first))
        == tail
    )


def test_coalesced_tail_drain_waits_for_the_complete_event_sequence() -> None:
    first = _first_event()
    summary = _running_summary()
    now = datetime.now(UTC)
    update = SessionUpdated(seq=10, summary=summary, last_in_commit=False)
    authorization = EntriesAppended(
        seq=10,
        session_id=summary.id,
        entries=(
            Entry(
                index=0,
                at=now,
                kind="authorization",
                turn_id=first.turn_id,
                facts={"actor_id": summary.owner},
            ),
        ),
        last_in_commit=True,
    )
    tail = _tail_event()

    release = asyncio.Event()
    waiting = asyncio.Event()

    async def events():
        waiting.set()
        await release.wait()
        for event in (update, authorization, tail):
            yield event

    async def run() -> ReplyText:
        task = asyncio.create_task(
            fixture_module._read_coalesced_tail(events(), summary.owner, summary.id, first)
        )
        await waiting.wait()
        assert not task.done()
        release.set()
        return await task

    assert asyncio.run(run()) == tail


@pytest.mark.parametrize(
    ("metadata", "authorization", "tail", "expected"),
    [
        ("wrong_sequence", "valid", "valid", "preterminal_authorization_invalid"),
        ("valid", "wrong_kind", "valid", "preterminal_authorization_invalid"),
        ("terminal", "valid", "valid", "preterminal_summary_invalid"),
        ("valid", "valid", "wrong_text", "buffer_content_mismatch"),
        ("valid", "valid", "wrong_attempt", "buffer_content_mismatch"),
    ],
)
def test_coalesced_tail_drain_refuses_unexpected_metadata_or_tail(
    metadata, authorization, tail, expected
) -> None:
    first = _first_event()
    summary = _running_summary()
    if metadata == "terminal":
        summary = summary.model_copy(update={"status": "closed", "active_turn_id": None})
    update = SessionUpdated(seq=10, summary=summary, last_in_commit=False)
    if metadata == "wrong_sequence":
        update = update.model_copy(update={"seq": 11})
    kind = "unexpected" if authorization == "wrong_kind" else "authorization"
    now = datetime.now(UTC)
    appended = EntriesAppended(
        seq=10,
        session_id=summary.id,
        entries=(
            Entry(
                index=0,
                at=now,
                kind=kind,
                turn_id=first.turn_id,
                facts={"actor_id": summary.owner},
            ),
        ),
        last_in_commit=True,
    )
    wanted_tail = _tail_event()
    if tail == "wrong_text":
        wanted_tail = wanted_tail.model_copy(update={"text": "incorrect"})
    elif tail == "wrong_attempt":
        wanted_tail = wanted_tail.model_copy(update={"attempt_id": "other-attempt"})

    async def events():
        yield update
        yield appended
        yield wanted_tail

    with pytest.raises(RuntimeError, match=f"benchmark_coalesced_{expected}"):
        asyncio.run(fixture_module._read_coalesced_tail(events(), summary.owner, summary.id, first))


def _first_event() -> ReplyText:
    return ReplyText(
        session_id="fabricated-session",
        turn_id="fabricated-turn",
        attempt_id="fabricated-attempt",
        offset=0,
        text="q",
    )


def _tail_event() -> ReplyText:
    return ReplyText(
        session_id="fabricated-session",
        turn_id="fabricated-turn",
        attempt_id="fabricated-attempt",
        offset=1,
        text="z" * 32_767,
    )


def _running_summary() -> SessionSummary:
    now = datetime.now(UTC)
    return SessionSummary(
        id="fabricated-session",
        owner="fabricated-owner",
        status="running",
        active_turn_id="fabricated-turn",
        created_at=now,
        updated_at=now,
    )


def test_byte_distribution_reports_actual_extrema() -> None:
    assert _summary([760, 1024, 32767]) == {
        "sample_count": 3,
        "min_bytes": 760,
        "max_bytes": 32767,
        "median_bytes": 1024.0,
        "p99_bytes": 32767,
    }


def test_queue_size_is_an_explicit_fixed_benchmark_input() -> None:
    with pytest.raises(ValueError, match="benchmark_memory_queue_size_invalid"):
        asyncio.run(benchmark.benchmark_follower_memory(queue_size=31))


def test_persisted_overflow_marker_before_full_queue_is_refused() -> None:
    async def early_marker():
        yield Lagged(missed=0)

    with pytest.raises(RuntimeError, match="benchmark_persisted_overflow_marker_order_invalid"):
        asyncio.run(benchmark_trial._drain_persisted_queue(early_marker()))


def test_persisted_repeated_overflow_marker_is_refused() -> None:
    queued = {
        EntriesAppended(
            seq=sequence + 1,
            session_id="fabricated-session",
            entries=(),
            last_in_commit=True,
        ).seq
        for sequence in range(32)
    }
    overflow = benchmark_trial._accept_persisted_overflow_marker(queued, 0, Lagged(missed=0))
    with pytest.raises(RuntimeError, match="benchmark_persisted_overflow_marker_order_invalid"):
        benchmark_trial._accept_persisted_overflow_marker(queued, overflow, Lagged(missed=0))


def test_cleanup_attempts_every_resource_and_surfaces_failure() -> None:
    calls: list[str] = []

    async def close_files() -> bool:
        calls.append("files")
        return True

    async def close_store() -> bool:
        calls.append("store")
        raise RuntimeError("fabricated_store_close_failure")

    async def close_host() -> bool:
        calls.append("host")
        return True

    with pytest.raises(RuntimeError, match="fabricated_store_close_failure"):
        asyncio.run(
            close_all_retained(
                [("files", close_files), ("store", close_store), ("host", close_host)]
            )
        )
    assert calls == ["host", "store", "files"]


def test_cleanup_survives_repeated_cancellation_and_then_reports_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> list[str]:
        calls: list[str] = []
        entered = asyncio.Event()
        release = asyncio.Event()
        observer = CancellationObserver()
        observer.install(selected_profiles_process, monkeypatch)

        async def close_blocked() -> bool:
            entered.set()
            await release.wait()
            calls.append("blocked")
            return True

        async def close_last() -> bool:
            calls.append("last")
            return True

        task = asyncio.create_task(
            close_all_retained([("last", close_last), ("blocked", close_blocked)])
        )
        try:
            await wait_event_or_task(entered, task)
            await wait_event_or_task(observer.entered, task)
            for acknowledgement in observer.acknowledged:
                task.cancel()
                await wait_event_or_task(acknowledgement, task)
                assert not task.done()
        finally:
            await settle_tasks((task,), release=release.set)
        with pytest.raises(asyncio.CancelledError):
            await task
        return calls

    assert asyncio.run(run()) == ["blocked", "last"]


def test_partial_fixture_setup_closes_every_acquired_resource(monkeypatch) -> None:
    closed: list[str] = []
    original_files_close = MemoryFiles.aclose
    original_store_close = JsonlSessionStore.aclose

    async def close_files(self) -> bool:
        closed.append("files")
        return await original_files_close(self)

    async def close_store(self) -> bool:
        closed.append("store")
        return await original_store_close(self)

    async def fail_host(cls, settings):
        del cls, settings
        raise RuntimeError("fabricated_host_open_failure")

    monkeypatch.setattr(MemoryFiles, "aclose", close_files)
    monkeypatch.setattr(JsonlSessionStore, "aclose", close_store)
    monkeypatch.setattr(ports_module.ClientHost, "open", classmethod(fail_host))
    with pytest.raises(RuntimeError, match="fabricated_host_open_failure"):
        asyncio.run(ports_module._fixture())
    assert closed == ["store", "files"]


def test_host_physical_wait_finishes_before_borrowed_store_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> list[str]:
        order: list[str] = []
        entered = asyncio.Event()
        release = asyncio.Event()
        observer = CancellationObserver()
        observer.install(selected_profiles_process, monkeypatch)

        class Host:
            async def aclose(self) -> bool:
                order.append("host-close-start")
                return False

            async def wait_closed(self) -> bool:
                order.append("host-wait-start")
                entered.set()
                await release.wait()
                order.append("host-closed")
                return True

        class Store:
            async def aclose(self) -> bool:
                assert "host-closed" in order
                order.append("store-closed")
                return True

        cleanup = asyncio.create_task(
            fixture_module._close_fixture(
                cast(fixture_module._Fixture, SimpleNamespace(host=Host(), store=Store()))
            )
        )
        try:
            await wait_event_or_task(entered, cleanup)
            await wait_event_or_task(observer.entered, cleanup)
            assert "store-closed" not in order
            for acknowledgement in observer.acknowledged:
                cleanup.cancel()
                await wait_event_or_task(acknowledgement, cleanup)
                assert not cleanup.done()
                assert "store-closed" not in order
        finally:
            await settle_tasks((cleanup,), release=release.set)
        with pytest.raises(asyncio.CancelledError):
            await cleanup
        return order

    assert asyncio.run(run()) == [
        "host-close-start",
        "host-wait-start",
        "host-closed",
        "store-closed",
    ]
