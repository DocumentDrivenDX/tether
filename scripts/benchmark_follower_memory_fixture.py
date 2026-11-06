"""Fabricated public-client fixture for follower allocation measurements."""

from __future__ import annotations

import asyncio
import gc
import math
import statistics
import tracemalloc
from collections.abc import AsyncIterator, Awaitable, Sequence
from dataclasses import dataclass

from benchmark_follower_memory_cleanup import cancel_and_join, run_retained
from benchmark_follower_memory_ports import (
    _COALESCED_TEXT_BYTES,
    _DEADLINE_SECONDS,
    _close_fixture,
    _close_stream,
    _finished,
    _Fixture,
    _fixture,
    _positive_growth,
)

from tether.client import Change, EntriesAppended, Lagged, ReplyText, SessionUpdated, TurnHandle
from tether.types.canonical import canonical


def _summary(values: Sequence[int]) -> dict[str, float | int]:
    if not values:
        raise ValueError("benchmark_memory_observations_empty")
    return {
        "sample_count": len(values),
        "min_bytes": min(values),
        "max_bytes": max(values),
        "median_bytes": float(statistics.median(values)),
        "p99_bytes": float(sorted(values)[math.ceil(len(values) * 0.99) - 1]),
    }


@dataclass
class _ConsumerState:
    condition: asyncio.Condition
    seen_seq: int = 0


async def _consume_first_live(stream: AsyncIterator[Change], state: _ConsumerState) -> ReplyText:
    while True:
        event = await asyncio.wait_for(anext(stream), timeout=_DEADLINE_SECONDS)
        if isinstance(event, ReplyText):
            return event
        if isinstance(event, (SessionUpdated, EntriesAppended)) and event.last_in_commit:
            async with state.condition:
                state.seen_seq = max(state.seen_seq, event.seq)
                state.condition.notify_all()
        elif isinstance(event, Lagged):
            continue


async def _coalesced_buffer_trial() -> dict[str, int]:
    fixture = await _fixture()
    stream: AsyncIterator[Change] | None = None
    consumer: asyncio.Task[ReplyText] | None = None
    handle: TurnHandle | None = None
    tracing = False
    try:
        summary = await fixture.client.create(ref="coalesced", spec="benchmark")
        fixture.model.configure_coalesced(summary.id)
        since = await fixture.store.for_actor(fixture.actor).watermark(fixture.actor.id)
        stream = fixture.client.follow(since=since)
        state = _ConsumerState(asyncio.Condition())
        consumer = asyncio.create_task(_consume_first_live(stream, state))
        await asyncio.wait_for(fixture.entered.wait(), timeout=_DEADLINE_SECONDS)
        gc.collect()
        tracemalloc.start()
        tracing = True
        baseline = tracemalloc.get_traced_memory()[0]
        handle = await fixture.client.send(summary.id, "fabricated input", spec="benchmark")
        if stream is None or consumer is None:
            raise RuntimeError("benchmark_coalesced_stream_not_started")
        return await _finish_coalesced(fixture, summary.id, handle, consumer, stream, baseline)
    finally:
        await run_retained(_cleanup_coalesced(fixture, stream, consumer, handle, tracing))


async def _cleanup_coalesced(
    fixture: _Fixture,
    stream: AsyncIterator[Change] | None,
    consumer: asyncio.Task[ReplyText] | None,
    handle: TurnHandle | None,
    tracing: bool,
) -> None:
    failures: list[BaseException] = []
    fixture.model.emit_coalesced.set()
    for event in fixture.model.release.values():
        event.set()
    if consumer is not None:
        await _try_cleanup(failures, cancel_and_join([consumer]))
    if stream is not None:
        await _try_cleanup(failures, _close_stream(stream))
    if handle is not None:
        await _try_cleanup(failures, _finished(handle, "q" + ("z" * (_COALESCED_TEXT_BYTES - 1))))
    if tracing and tracemalloc.is_tracing():
        tracemalloc.stop()
    await _try_cleanup(failures, _close_fixture(fixture))
    if failures:
        raise failures[0]


async def _try_cleanup(failures: list[BaseException], operation: Awaitable[object]) -> None:
    try:
        await operation
    except BaseException as error:
        failures.append(error)


async def _finish_coalesced(
    fixture: _Fixture,
    session_id: str,
    handle: TurnHandle,
    consumer: asyncio.Task[ReplyText],
    stream: AsyncIterator[Change],
    baseline: int,
) -> dict[str, int]:
    await fixture.model.wait_started(session_id, 1)
    fixture.model.emit_coalesced.set()
    first = await asyncio.wait_for(consumer, timeout=_DEADLINE_SECONDS)
    fixture.model.release_attempt(session_id, 1)
    await _finished(handle, "q" + ("z" * (_COALESCED_TEXT_BYTES - 1)))
    gc.collect()
    full_bytes = tracemalloc.get_traced_memory()[0]
    buffered = await _read_coalesced_tail(stream, fixture.actor.id, session_id, first)
    expected_tail = "z" * (_COALESCED_TEXT_BYTES - 1)
    if (
        buffered.text != expected_tail
        or first.text != "q"
        or len(buffered.text) != _COALESCED_TEXT_BYTES - 1
        or first.offset != 0
        or buffered.offset != 1
        or first.attempt_id != buffered.attempt_id
        or first.turn_id != buffered.turn_id
        or first.session_id != buffered.session_id
    ):
        raise RuntimeError("benchmark_coalesced_buffer_shape_mismatch")
    while True:
        try:
            event = await asyncio.wait_for(anext(stream), timeout=0.05)
        except TimeoutError:
            break
        if isinstance(event, ReplyText):
            raise RuntimeError("benchmark_coalesced_unexpected_additional_text")
        if isinstance(event, Lagged) and event.missed:
            raise RuntimeError("benchmark_coalesced_buffer_lagged")
    delta_count = fixture.model.delta_counts[(session_id, 1)]
    if delta_count != 33:
        raise RuntimeError("benchmark_coalesced_delta_count_mismatch")
    return {
        "baseline_current_bytes": baseline,
        "full_retained_current_bytes": full_bytes,
        "python_growth_bytes": _positive_growth(full_bytes, baseline),
        "coalesced_live_events": 1,
        "coalesced_text_bytes": len(buffered.text),
        "coalesced_total_model_text_bytes": _COALESCED_TEXT_BYTES,
        "coalesced_later_delta_chunks": delta_count - 1,
        "coalesced_event_serialized_bytes": len(canonical(buffered.model_dump(mode="json"))),
    }


async def _read_coalesced_tail(
    stream: AsyncIterator[Change], owner: str, session_id: str, first: ReplyText
) -> ReplyText:
    async with asyncio.timeout(_DEADLINE_SECONDS):
        event = await anext(stream)
        if isinstance(event, SessionUpdated):
            active_turn = event.summary.active_turn_id
            if (
                event.seq <= 0
                or event.summary.id != session_id
                or event.summary.owner != owner
                or event.summary.status != "running"
                or not active_turn
                or active_turn != first.turn_id
                or event.last_in_commit
            ):
                raise RuntimeError("benchmark_coalesced_preterminal_summary_invalid")
            appended = await anext(stream)
            if (
                not isinstance(appended, EntriesAppended)
                or appended.seq != event.seq
                or appended.session_id != session_id
                or not appended.last_in_commit
                or not appended.entries
                or any(
                    entry.kind != "authorization"
                    or entry.turn_id != active_turn
                    or entry.facts.get("actor_id") != owner
                    for entry in appended.entries
                )
            ):
                raise RuntimeError("benchmark_coalesced_preterminal_authorization_invalid")
            event = await anext(stream)
    if (
        not isinstance(event, ReplyText)
        or event.text != "z" * (_COALESCED_TEXT_BYTES - 1)
        or event.offset != 1
        or event.session_id != session_id
        or event.turn_id != first.turn_id
        or event.attempt_id != first.attempt_id
    ):
        raise RuntimeError("benchmark_coalesced_buffer_content_mismatch")
    return event
