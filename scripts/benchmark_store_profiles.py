"""Explicit-capacity local JSONL and MemoryFiles store reference profiles."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal, TypedDict

from benchmark_measurements import Distribution, distribution
from selected_profiles_process import await_retained

from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.stores.memory import MemoryFiles
from tether.types.execution import NewEntry
from tether.types.storage import SessionInit, SessionQuery, StorageLimits

StorageProfile = Literal["local-posix-jsonl", "memoryfiles-jsonl-reference-capacity"]
OWNER = "fabricated-benchmark"
_PAGE_SIZE = 100
_SPREAD_POSITIONS = 100


class StoreProfileResult(TypedDict):
    profile: StorageProfile
    workload: dict[str, object]
    limits: dict[str, int | float]
    results: dict[str, object]
    durability: Literal["os", "none"]
    checks: dict[str, object]
    journal_bytes: int | None
    scope: str


async def _measure(operation: Callable[[], Awaitable[object]], samples: int) -> Distribution:
    await operation()
    observations: list[float] = []
    for _ in range(samples):
        started = time.perf_counter_ns()
        await operation()
        observations.append((time.perf_counter_ns() - started) / 1_000_000)
    return distribution(observations)


async def _close_files(files: JsonlRoot | MemoryFiles) -> None:
    closed = await await_retained(asyncio.create_task(files.aclose()))
    if not closed:
        wait_closed = getattr(files, "wait_closed", None)
        if wait_closed is not None:
            closed = await await_retained(asyncio.create_task(wait_closed()))
    if not closed:
        raise RuntimeError("benchmark_store_resources_not_closed")


async def _close_store(store: JsonlSessionStore) -> None:
    closed = await await_retained(asyncio.create_task(store.aclose()))
    if not closed:
        wait_closed = getattr(store.files, "wait_closed", None)
        if wait_closed is not None:
            closed = await await_retained(asyncio.create_task(wait_closed()))
    if not closed:
        raise RuntimeError("benchmark_store_resources_not_closed")


def page_positions(entry_count: int, *, count: int = _SPREAD_POSITIONS) -> tuple[int, ...]:
    """Return a deterministic, unique, evenly spaced set of valid page starts."""
    if count < 2 or entry_count < _PAGE_SIZE + count - 1:
        raise ValueError("benchmark_page_position_range_too_small")
    maximum = entry_count - _PAGE_SIZE
    positions = tuple(index * maximum // (count - 1) for index in range(count))
    if len(set(positions)) != count:
        raise ValueError("benchmark_page_positions_not_unique")
    return positions


async def _page(store: JsonlSessionStore, session: str, start: int) -> tuple[int, ...]:
    page = await store.entries(session, OWNER, start=start, limit=_PAGE_SIZE)
    indices = tuple(entry.index for entry in page.items)
    expected = tuple(range(start, start + _PAGE_SIZE))
    if indices != expected:
        raise RuntimeError("benchmark_entry_page_indices_mismatch")
    return indices


async def _seed(
    store: JsonlSessionStore, sessions: int, entries_per_session: int, large: int
) -> str:
    values = tuple(
        NewEntry(kind="benchmark", facts={"fabricated": "x" * 768})
        for _ in range(entries_per_session)
    )
    for index in range(sessions):
        summary = await store.create(OWNER, SessionInit(ref=f"fabricated-{index}"))
        async with store.turn(summary.id, OWNER, turn_id="seed") as log:
            await log.append(values)
    summary = await store.create(OWNER, SessionInit(ref="fabricated-large"))
    async with store.turn(summary.id, OWNER, turn_id="seed-large") as log:
        remaining = large
        while remaining:
            size = min(1000, remaining)
            await log.heartbeat()
            await log.append(
                tuple(
                    NewEntry(kind="benchmark", facts={"fabricated": "x" * 768}) for _ in range(size)
                )
            )
            remaining -= size
    return summary.id


async def _reads(
    store: JsonlSessionStore, session: str, large: int, samples: int
) -> dict[str, object]:
    async def list_page() -> object:
        return await store.list(SessionQuery(owner=OWNER), limit=50)

    async def view() -> object:
        return await store.get(session, OWNER)

    beginning, middle, end = 0, (large - _PAGE_SIZE) // 2, large - _PAGE_SIZE
    positions = page_positions(large)
    # Correctness is checked independently before timing every sampled position.
    for start in sorted({beginning, middle, end, *positions}):
        await _page(store, session, start)

    async def measured_page(start: int) -> Distribution:
        async def operation() -> object:
            return await _page(store, session, start)

        return await _measure(operation, samples)

    spread_observations: list[float] = []
    for start in positions:
        started = time.perf_counter_ns()
        await _page(store, session, start)
        spread_observations.append((time.perf_counter_ns() - started) / 1_000_000)
    return {
        "list_50": await _measure(list_page, samples),
        "view": await _measure(view, samples),
        "entries_100_beginning": await measured_page(beginning),
        "entries_100_middle": await measured_page(middle),
        "entries_100_end": await measured_page(end),
        "entries_100_spread": {
            **distribution(spread_observations),
            "positions": list(positions),
        },
    }


async def benchmark_store_profile(
    *,
    profile: StorageProfile,
    root_path: Path,
    sessions: int = 10_000,
    entries_per_session: int = 200,
    large_session_entries: int = 100_000,
    samples: int = 100,
) -> StoreProfileResult:
    if min(sessions, entries_per_session, large_session_entries) <= 0 or samples < 100:
        raise ValueError("benchmark_workload_or_sample_count_invalid")
    limits = StorageLimits(max_index_bytes=4 * 1024**3, lease_seconds=300)
    if profile == "local-posix-jsonl":
        files = await JsonlRoot.open(root_path, limits=limits)
        durability: Literal["os", "none"] = "os"
    elif profile == "memoryfiles-jsonl-reference-capacity":
        files = MemoryFiles(limits)
        durability = "none"
    else:
        raise ValueError("benchmark_store_profile_unknown")

    store: JsonlSessionStore | None = None
    primary_error: BaseException | None = None
    try:
        store = await JsonlSessionStore.open(files)
        session = await _seed(store, sessions, entries_per_session, large_session_entries)
        results = await _reads(store, session, large_session_entries, samples)
        async with store.turn(session, OWNER, turn_id="measurement") as log:

            async def append_four() -> object:
                return await log.append(
                    tuple(
                        NewEntry(kind="benchmark", facts={"fabricated": "x" * 768})
                        for _ in range(4)
                    )
                )

            results["append_four"] = await _measure(append_four, samples)
        journal_bytes = (
            (await asyncio.to_thread(root_path.joinpath("journal.jsonl").stat)).st_size
            if profile == "local-posix-jsonl"
            else None
        )
    except BaseException as error:
        primary_error = error
        raise
    finally:
        try:
            if store is None:
                await _close_files(files)
            else:
                await _close_store(store)
        except BaseException:
            if primary_error is None:
                raise
            primary_error.add_note("benchmark_store_cleanup_failed")

    starts = page_positions(large_session_entries)
    return {
        "profile": profile,
        "workload": {
            "sessions": sessions,
            "entries_per_session": entries_per_session,
            "large_session_entries": large_session_entries,
            "samples": samples,
            "entry_facts_bytes": 768,
            "page_positions": list(starts),
        },
        "limits": {
            "max_index_bytes": limits.max_index_bytes,
            "lease_seconds": limits.lease_seconds,
            "feed_retention": limits.feed_retention,
        },
        "results": results,
        "durability": durability,
        "checks": {
            "page_ranges_checked": len(
                set(
                    (
                        0,
                        (large_session_entries - _PAGE_SIZE) // 2,
                        large_session_entries - _PAGE_SIZE,
                        *starts,
                    )
                )
            ),
            "page_size": _PAGE_SIZE,
            "page_indices_exact": True,
        },
        "journal_bytes": journal_bytes,
        "scope": (
            "Store list/view/entry/append only. memoryfiles-jsonl-reference-capacity uses "
            "MemoryFiles and durability=none; it is not VolatileSettings and changes no "
            "product defaults. Spread pages are 100 deterministic positions, not every window."
        ),
    }
