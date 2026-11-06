"""True retained-cursor reconnect and feed-floor resnapshot measurements."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from benchmark_measurements import distribution
from selected_profiles_process import await_retained

from tether.client import (
    Actor,
    Change,
    ClientHost,
    ClientProtocol,
    ClientSettings,
    EntriesAppended,
    ProvidedStorage,
    ResyncRequired,
    SessionUpdated,
)
from tether.history.jsonl import JsonlHistory
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.stores.memory import MemoryFiles
from tether.types.storage import StorageLimits

StorageKind = Literal["posix", "volatile"]
_OWNER = "fabricated-benchmark"
_RETENTION = 8
_RETAINED_MISSED_COMMITS = 3
_EVENT_TIMEOUT_SECONDS = 30


async def _event(stream: AsyncIterator[Change]) -> Change:
    return await asyncio.wait_for(anext(stream), _EVENT_TIMEOUT_SECONDS)


async def _checked_commit(
    stream: AsyncIterator[Change], expected_seq: int, *, first: Change | None = None
) -> int:
    change = await _event(stream) if first is None else first
    saw_summary = False
    saw_entries = False
    while True:
        if not isinstance(change, (SessionUpdated, EntriesAppended)):
            raise RuntimeError("benchmark_reconnect_expected_persisted_change")
        if change.seq != expected_seq:
            raise RuntimeError("benchmark_reconnect_sequence_mismatch")
        saw_summary = saw_summary or isinstance(change, SessionUpdated)
        saw_entries = saw_entries or isinstance(change, EntriesAppended)
        if change.last_in_commit:
            if not saw_summary or not saw_entries or not isinstance(change, EntriesAppended):
                raise RuntimeError("benchmark_reconnect_commit_variants_incomplete")
            return change.seq
        if not isinstance(change, SessionUpdated):
            raise RuntimeError("benchmark_reconnect_commit_variant_order_invalid")
        change = await _event(stream)


async def _close_stream(stream: AsyncIterator[Change]) -> None:
    close = getattr(stream, "aclose", None)
    if close is not None:
        await close()


async def _close_fixture(
    host: ClientHost | None,
    store: JsonlSessionStore | None,
    files: JsonlRoot | MemoryFiles,
) -> None:
    failures: list[BaseException] = []
    if host is not None:
        try:
            await _require_closed(
                host.aclose, host.wait_closed, "benchmark_reconnect_host_not_closed"
            )
        except BaseException as error:
            failures.append(error)

    try:
        if store is None:
            close = files.aclose
            wait_closed = getattr(files, "wait_closed", None)
        else:
            close = store.aclose
            wait_closed = getattr(store.files, "wait_closed", None)
        await _require_closed(
            close,
            wait_closed,
            "benchmark_reconnect_resources_not_closed",
        )
    except BaseException as error:
        failures.append(error)
    if failures:
        raise failures[0]


async def _require_closed(
    close: Callable[[], Awaitable[bool]],
    wait_closed: Callable[[], Awaitable[bool]] | None,
    error_code: str,
) -> None:
    closed = await close()
    if not closed and wait_closed is not None:
        closed = await wait_closed()
    if not closed:
        raise RuntimeError(error_code)


async def _close_fixture_retained(
    host: ClientHost | None,
    store: JsonlSessionStore | None,
    files: JsonlRoot | MemoryFiles,
) -> None:
    await await_retained(asyncio.create_task(_close_fixture(host, store, files)))


async def _anchor_cursor(client: ClientProtocol, session_id: str) -> int:
    cursor = (await client.snapshot(session_id)).seq
    stream = client.follow(session_id=session_id, since=cursor)
    waiter = asyncio.create_task(_event(stream))
    primary_error: BaseException | None = None
    try:
        summary = await client.update(session_id, title="Fabricated reconnect anchor")
        first = await asyncio.wait_for(waiter, _EVENT_TIMEOUT_SECONDS)
        await _checked_commit(stream, summary.last_seq, first=first)
        return summary.last_seq
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if not waiter.done():
            waiter.cancel()
        cleanup = asyncio.create_task(_settle_waiter_and_stream(waiter, stream))
        try:
            await await_retained(cleanup)
        except BaseException:
            if primary_error is None:
                raise
            primary_error.add_note("benchmark_reconnect_stream_cleanup_failed")


async def _settle_waiter_and_stream(
    waiter: asyncio.Task[Change], stream: AsyncIterator[Change]
) -> None:
    await asyncio.gather(waiter, return_exceptions=True)
    await _close_stream(stream)


async def _retained_cursor_case(
    client: ClientProtocol, session_id: str, feed_retention: int, samples: int
) -> dict[str, object]:
    if feed_retention < _RETAINED_MISSED_COMMITS:
        raise ValueError("benchmark_retention_too_small_for_retained_case")
    observations: list[float] = []
    total_missed = 0
    checked = 0
    for _ in range(samples):
        cursor = await _anchor_cursor(client, session_id)
        expected: list[int] = []
        for index in range(_RETAINED_MISSED_COMMITS):
            summary = await client.update(
                session_id, title=f"Fabricated disconnected commit {index}"
            )
            expected.append(summary.last_seq)
        started = time.perf_counter_ns()
        stream = client.follow(session_id=session_id, since=cursor)
        try:
            observed = [await _checked_commit(stream, sequence) for sequence in expected]
        finally:
            await _close_stream(stream)
        if observed != expected:
            raise RuntimeError("benchmark_reconnect_order_mismatch")
        observations.append((time.perf_counter_ns() - started) / 1_000_000)
        total_missed += len(expected)
        checked += len(observed)
    return {
        "sample_count": samples,
        "reconnect_to_replay_complete": distribution(observations),
        "configured_feed_retention": feed_retention,
        "missed_commits_per_sample": _RETAINED_MISSED_COMMITS,
        "total_missed_commits": total_missed,
        "exact_ordered_last_in_commit_count": checked,
    }


async def _collect_retained_boundary(
    client: ClientProtocol, session_id: str, since: int, expected: list[int]
) -> None:
    stream = client.follow(session_id=session_id, since=since)
    try:
        observed = [await _checked_commit(stream, sequence) for sequence in expected]
    finally:
        await _close_stream(stream)
    if observed != expected:
        raise RuntimeError("benchmark_retention_boundary_order_mismatch")


async def _retention_floor_case(
    client: ClientProtocol, session_id: str, feed_retention: int, samples: int
) -> dict[str, object]:
    # Establish the exact boundary oracle: oldest-retained-minus-one is replayable.
    for index in range(feed_retention + 2):
        await client.update(session_id, title=f"Fabricated boundary seed {index}")
    latest = (await client.snapshot(session_id)).seq
    boundary = latest - feed_retention
    await _collect_retained_boundary(
        client, session_id, boundary, list(range(boundary + 1, latest + 1))
    )

    observations: list[float] = []
    resyncs = 0
    snapshots = 0
    for sample in range(samples):
        stale_cursor = (await client.snapshot(session_id)).seq
        latest_summary = None
        for index in range(feed_retention + 1):
            latest_summary = await client.update(
                session_id, title=f"Fabricated expired cursor {sample}-{index}"
            )
        if latest_summary is None:
            raise RuntimeError("benchmark_retention_floor_seed_missing")
        started = time.perf_counter_ns()
        stream = client.follow(session_id=session_id, since=stale_cursor)
        try:
            change = await _event(stream)
        finally:
            await _close_stream(stream)
        if not isinstance(change, ResyncRequired) or change.reason != "too_old":
            raise RuntimeError("benchmark_retention_floor_resync_missing")
        resyncs += 1
        snapshot = await client.snapshot(session_id)
        if snapshot.seq != latest_summary.last_seq:
            raise RuntimeError("benchmark_retention_floor_snapshot_mismatch")
        snapshots += 1
        observations.append((time.perf_counter_ns() - started) / 1_000_000)

    # One sequence below the exact boundary must refuse replay and resnapshot.
    latest = (await client.snapshot(session_id)).seq
    boundary = latest - feed_retention
    below = client.follow(session_id=session_id, since=boundary - 1)
    try:
        change = await _event(below)
    finally:
        await _close_stream(below)
    if not isinstance(change, ResyncRequired) or change.reason != "too_old":
        raise RuntimeError("benchmark_retention_floor_negative_control_failed")
    return {
        "sample_count": samples,
        "reconnect_to_resync_snapshot": distribution(observations),
        "configured_feed_retention": feed_retention,
        "observed_too_old_resync_count": resyncs,
        "snapshot_at_latest_watermark_count": snapshots,
        "boundary_since_replayed": True,
        "one_before_boundary_resynced": True,
    }


async def reconnect_profile(
    client: ClientProtocol,
    session_id: str,
    *,
    samples: int,
    feed_retention: int,
) -> dict[str, object]:
    if samples < 1 or feed_retention < _RETAINED_MISSED_COMMITS:
        raise ValueError("benchmark_reconnect_profile_invalid")
    retained = await _retained_cursor_case(client, session_id, feed_retention, samples)
    floor = await _retention_floor_case(client, session_id, feed_retention, samples)
    return {"retained_cursor": retained, "retention_floor": floor}


async def benchmark_reconnect(
    config: ClientSettings, *, samples: int, storage: StorageKind
) -> dict[str, object]:
    if samples < 1:
        raise ValueError("benchmark_reconnect_samples_invalid")
    limits = StorageLimits(
        max_index_bytes=4 * 1024**3,
        lease_seconds=300,
        feed_retention=_RETENTION,
    )
    if storage == "posix":
        with TemporaryDirectory(prefix="tether-reconnect-benchmark-") as directory:
            files = await JsonlRoot.open(Path(directory), limits=limits)
            return await _run_fixture(config, files, samples, "local-posix-jsonl-public-client")
    if storage == "volatile":
        files = MemoryFiles(limits)
        return await _run_fixture(config, files, samples, "volatile-public-client")
    raise ValueError("benchmark_reconnect_storage_unknown")


async def _run_fixture(
    config: ClientSettings,
    files: JsonlRoot | MemoryFiles,
    samples: int,
    profile: str,
) -> dict[str, object]:
    store: JsonlSessionStore | None = None
    host: ClientHost | None = None
    primary_error: BaseException | None = None
    try:
        store = await JsonlSessionStore.open(files)
        history = await JsonlHistory.open(files)
        borrowed = replace(
            config,
            storage=ProvidedStorage(store=store, history=history),
            resume="off",
        )
        host = await ClientHost.open(borrowed)
        client = host.client(Actor(id=_OWNER))
        session = await client.create(spec="demo", title="Fabricated reconnect")
        result = await reconnect_profile(
            client,
            session.id,
            samples=samples,
            feed_retention=files.limits.feed_retention,
        )
    except BaseException as error:
        primary_error = error
        raise
    finally:
        try:
            await _close_fixture_retained(host, store, files)
        except BaseException:
            if primary_error is None:
                raise
            primary_error.add_note("benchmark_reconnect_cleanup_failed")
    return {
        "profile": profile,
        "storage_durability": files.durability,
        "feed_retention": files.limits.feed_retention,
        "client_limits": {
            "follower_queue_size": config.client_limits.follower_queue_size,
            "max_followers": config.client_limits.max_followers,
        },
        "results": result,
        "scope": (
            "Independent real public-client fixture with feed_retention=8; this is not "
            "the main host's default retention or a transport interruption test."
        ),
    }
