"""Separate whole-process follower envelope measurement."""

from __future__ import annotations

import asyncio
import gc
import resource
import sys
import tracemalloc
from collections.abc import AsyncIterator, Sequence

from benchmark_follower_memory_cleanup import cancel_and_join, run_retained
from benchmark_follower_memory_ports import (
    _DEADLINE_SECONDS,
    _close_fixture,
    _close_stream,
    _Fixture,
    _fixture,
    _positive_growth,
    _read_next,
)

from tether.client import Change


def _process_peak_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


async def _aggregate_followers(count: int) -> dict[str, int]:
    fixture = await _fixture(max_followers=count)
    streams: list[AsyncIterator[Change]] = []
    readers: list[asyncio.Task[Change]] = []
    try:
        control = await fixture.client.create(ref="aggregate", spec="benchmark")
        since = await fixture.store.for_actor(fixture.actor).watermark(fixture.actor.id)
        gc.collect()
        tracemalloc.start()
        baseline = tracemalloc.get_traced_memory()[0]
        rss_before = _process_peak_rss_bytes()
        streams = [fixture.client.follow(since=since) for _ in range(count)]
        readers = [asyncio.create_task(_read_next(stream)) for stream in streams]
        for _ in range(count):
            await asyncio.wait_for(fixture.registered.get(), timeout=_DEADLINE_SECONDS)
        await fixture.client.update(control.id, title="aggregate-ready")
        events = await asyncio.wait_for(asyncio.gather(*readers), timeout=_DEADLINE_SECONDS)
        sequences = {getattr(event, "seq", None) for event in events}
        if len(events) != count or len(sequences) != 1 or None in sequences:
            raise RuntimeError("benchmark_aggregate_follower_delivery_mismatch")
        current = tracemalloc.get_traced_memory()[0]
        return {
            "registered_followers": count,
            "ready_followers": len(events),
            "python_retained_growth_bytes": _positive_growth(current, baseline),
            "whole_process_peak_rss_before_bytes": rss_before,
            "whole_process_peak_rss_after_bytes": _process_peak_rss_bytes(),
        }
    finally:
        await run_retained(_cleanup_aggregate(fixture, readers, streams))


async def _cleanup_aggregate(
    fixture: _Fixture,
    readers: Sequence[asyncio.Task[Change]],
    streams: Sequence[AsyncIterator[Change]],
) -> None:
    failures: list[BaseException] = []
    try:
        await cancel_and_join(readers)
    except BaseException as error:
        failures.append(error)
    for stream in streams:
        try:
            await _close_stream(stream)
        except BaseException as error:
            failures.append(error)
    if tracemalloc.is_tracing():
        tracemalloc.stop()
    try:
        await _close_fixture(fixture)
    except BaseException as error:
        failures.append(error)
    if failures:
        raise failures[0]
