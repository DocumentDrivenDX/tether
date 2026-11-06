"""Single-follower persisted and live buffer measurement trials."""

from __future__ import annotations

import asyncio
import gc
import tracemalloc
from collections.abc import AsyncIterator, Sequence

from benchmark_follower_memory_cleanup import cancel_and_join, run_retained
from benchmark_follower_memory_fixture import (
    _coalesced_buffer_trial,
    _consume_first_live,
    _ConsumerState,
    _finished,
)
from benchmark_follower_memory_ports import (
    _DEADLINE_SECONDS,
    _HELD_TURNS,
    _QUEUE_SIZE,
    _TEXT_BYTES,
    _close_fixture,
    _close_stream,
    _consume_commit,
    _Fixture,
    _fixture,
    _GatedModel,
    _positive_growth,
)

from tether.client import Change, EntriesAppended, Lagged, ReplyText, SessionUpdated, TurnHandle
from tether.types.canonical import canonical


async def _read_next(stream: AsyncIterator[Change]) -> Change:
    return await anext(stream)


async def _fill_persisted_queue(fixture: _Fixture, control_id: str) -> dict[str, int]:
    since = await fixture.store.for_actor(fixture.actor).watermark(fixture.actor.id)
    await _preseed_persisted_changes(fixture, control_id)
    stream: AsyncIterator[Change] | None = None
    first_commit: asyncio.Task[int] | None = None
    tracing = False
    try:
        gc.collect()
        tracemalloc.start()
        tracing = True
        stream = fixture.client.follow(session_id=control_id, since=since)
        first_commit = asyncio.create_task(_consume_commit(stream))
        await asyncio.wait_for(fixture.entered.wait(), timeout=_DEADLINE_SECONDS)
        empty_bytes = tracemalloc.get_traced_memory()[0]
        fixture.allow_changes.set()
        first_seq = await asyncio.wait_for(first_commit, timeout=_DEADLINE_SECONDS)
        return await _measure_persisted_fill(fixture, stream, since, first_seq, empty_bytes)
    finally:
        fixture.allow_changes.set()
        fixture.release_change_tail.set()
        await _cleanup_persisted(stream, first_commit, tracing)


async def _preseed_persisted_changes(fixture: _Fixture, control_id: str) -> None:
    await fixture.client.update(control_id, title="ready")
    for index in range(_QUEUE_SIZE + 1):
        await fixture.client.update(control_id, title=f"queued-{index}")
    await fixture.client.update(control_id, title="queued-overflow")


async def _measure_persisted_fill(
    fixture: _Fixture,
    stream: AsyncIterator[Change],
    since: int,
    first_seq: int,
    empty_bytes: int,
) -> dict[str, int]:
    if first_seq == since:
        raise RuntimeError("benchmark_persisted_ready_commit_missing")
    first_fetched, first_advanced = await _first_change_acknowledgements(fixture)
    if first_fetched != first_seq or first_advanced != first_seq:
        raise RuntimeError("benchmark_persisted_first_commit_barrier_mismatch")
    fixture.release_change_tail.set()
    expected_queued = await _expected_queued_sequences(fixture, first_seq)
    gc.collect()
    full_bytes = tracemalloc.get_traced_memory()[0]
    queued, overflow = await _drain_persisted_queue(stream)
    if queued != expected_queued or overflow != 1:
        raise RuntimeError("benchmark_persisted_queue_occupancy_mismatch")
    return _persisted_result(empty_bytes, full_bytes, len(queued), overflow)


async def _first_change_acknowledgements(fixture: _Fixture) -> tuple[int, int]:
    fetched = await asyncio.wait_for(fixture.fetched.get(), timeout=_DEADLINE_SECONDS)
    advanced = await asyncio.wait_for(fixture.advanced.get(), timeout=_DEADLINE_SECONDS)
    return fetched, advanced


async def _expected_queued_sequences(fixture: _Fixture, first_seq: int) -> set[int]:
    advanced = await _unique_sequences(fixture.advanced, _QUEUE_SIZE, first_seq)
    fetched = await _unique_sequences(fixture.fetched, _QUEUE_SIZE + 1, first_seq)
    expected = set(sorted(fetched)[:_QUEUE_SIZE])
    if len(advanced) != _QUEUE_SIZE or len(expected) != _QUEUE_SIZE:
        raise RuntimeError("benchmark_persisted_queue_advance_ack_missing")
    return expected


async def _unique_sequences(queue: asyncio.Queue[int], count: int, after: int) -> set[int]:
    values: set[int] = set()
    while len(values) < count:
        sequence = await asyncio.wait_for(queue.get(), timeout=_DEADLINE_SECONDS)
        if sequence > after:
            values.add(sequence)
    return values


async def _drain_persisted_queue(stream: AsyncIterator[Change]) -> tuple[set[int], int]:
    queued: set[int] = set()
    overflow = 0
    while len(queued) < _QUEUE_SIZE or overflow < 1:
        event = await asyncio.wait_for(anext(stream), timeout=_DEADLINE_SECONDS)
        if isinstance(event, Lagged):
            overflow = _accept_persisted_overflow_marker(queued, overflow, event)
        elif isinstance(event, (SessionUpdated, EntriesAppended)):
            if overflow:
                raise RuntimeError("benchmark_persisted_queue_event_order_invalid")
            queued.add(event.seq)
    return queued, overflow


def _accept_persisted_overflow_marker(queued: set[int], overflow_count: int, event: Lagged) -> int:
    if event.missed != 0:
        raise RuntimeError("benchmark_persisted_unexpected_lag")
    if len(queued) != _QUEUE_SIZE or overflow_count != 0:
        raise RuntimeError("benchmark_persisted_overflow_marker_order_invalid")
    return overflow_count + 1


def _persisted_result(empty: int, full: int, queued: int, overflow: int) -> dict[str, int]:
    return {
        "empty_follower_current_bytes": empty,
        "full_retained_current_bytes": full,
        "python_growth_bytes": _positive_growth(full, empty),
        "persisted_changes_enqueued_at_snapshot": _QUEUE_SIZE,
        "persisted_changes_drained_before_overflow_marker": queued,
        "persisted_overflow_marker_after_drain": overflow,
        "persisted_queue_capacity": _QUEUE_SIZE,
        "persisted_queue_occupancy": _QUEUE_SIZE,
        "journal_commits_preseeded_before_baseline": _QUEUE_SIZE + 3,
    }


async def _cleanup_persisted(
    stream: AsyncIterator[Change] | None,
    first_commit: asyncio.Task[int] | None,
    tracing: bool,
) -> None:
    failures: list[BaseException] = []
    if first_commit is not None:
        try:
            await cancel_and_join([first_commit])
        except BaseException as error:
            failures.append(error)
    if stream is not None:
        try:
            await _close_stream(stream)
        except BaseException as error:
            failures.append(error)
    if tracing and tracemalloc.is_tracing():
        tracemalloc.stop()
    if failures:
        raise failures[0]


async def _wait_seen(state: _ConsumerState, seq: int) -> None:
    async with state.condition:
        await asyncio.wait_for(
            state.condition.wait_for(lambda: state.seen_seq >= seq),
            timeout=_DEADLINE_SECONDS,
        )


async def _advance_to_watermark(
    stream: AsyncIterator[Change], state: _ConsumerState, target: int
) -> None:
    while state.seen_seq < target:
        event = await asyncio.wait_for(anext(stream), timeout=_DEADLINE_SECONDS)
        if isinstance(event, (SessionUpdated, EntriesAppended)) and event.last_in_commit:
            async with state.condition:
                state.seen_seq = max(state.seen_seq, event.seq)
                state.condition.notify_all()
        elif isinstance(event, Lagged) and event.missed:
            raise RuntimeError("benchmark_live_persisted_feed_lagged")
        elif isinstance(event, ReplyText):
            raise RuntimeError("benchmark_live_event_arrived_before_fill_barrier")


async def _start_held_turns(
    fixture: _Fixture, held: Sequence[str], state: _ConsumerState
) -> list[TurnHandle]:
    handles: list[TurnHandle] = []
    for session_id in held:
        handles.append(await fixture.client.send(session_id, "fabricated input", spec="benchmark"))
        await fixture.model.wait_started(session_id, 1)
        current = await fixture.store.for_actor(fixture.actor).watermark(fixture.actor.id)
        await _wait_seen(state, current)
    return handles


async def _fill_live_slots(
    fixture: _Fixture,
    held: Sequence[str],
    handles: list[TurnHandle],
    state: _ConsumerState,
    stream: AsyncIterator[Change],
    consumer: asyncio.Task[ReplyText],
) -> ReplyText:
    primary = held[0]
    fixture.model.emit_primary_first.set()
    first = await asyncio.wait_for(consumer, timeout=_DEADLINE_SECONDS)

    fixture.model.release_attempt(primary, 1)
    await _finished(handles[0], "a" * _TEXT_BYTES)
    second_ordinal = fixture.model.register_primary_retry("primary_second")
    handles.append(await fixture.client.send(primary, "fabricated retry", spec="benchmark"))
    await fixture.model.wait_started(primary, second_ordinal)
    current = await fixture.store.for_actor(fixture.actor).watermark(fixture.actor.id)
    await _advance_to_watermark(stream, state, current)

    fixture.model.emit_primary_second.set()
    await fixture.model.wait_published(primary, second_ordinal)
    fixture.model.emit_others.set()
    await asyncio.wait_for(
        asyncio.gather(*(fixture.model.wait_published(session, 1) for session in held[1:])),
        timeout=_DEADLINE_SECONDS,
    )
    return first


async def _drain_live_buffer(
    stream: AsyncIterator[Change], first: ReplyText, model: _GatedModel
) -> list[ReplyText]:
    events = [first]
    missed = 0
    zero_lag_markers = 0
    while len(events) < _HELD_TURNS + 1 or missed != 1:
        try:
            event = await asyncio.wait_for(anext(stream), timeout=_DEADLINE_SECONDS)
        except TimeoutError:
            raise RuntimeError(
                "benchmark_live_drain_timeout:"
                f"{len(events)}:{missed}:{zero_lag_markers}:"
                f"{sum(event.session_id == model.primary_session for event in events)}:"
                f"{len({event.session_id for event in events})}:"
                ",".join(
                    str(model.identities.get(event.attempt_id, ("", 0))[1]) for event in events
                )
            ) from None
        if isinstance(event, ReplyText):
            events.append(event)
        elif isinstance(event, Lagged):
            if event.missed == 1:
                missed += 1
            elif event.missed != 0:
                raise RuntimeError("benchmark_live_lag_value_invalid")
            else:
                zero_lag_markers += 1
    return events


async def _fill_live_buffer(fixture: _Fixture, held: Sequence[str]) -> dict[str, int]:
    fixture.model.configure(held)
    since = await fixture.store.for_actor(fixture.actor).watermark(fixture.actor.id)
    stream = fixture.client.follow(since=since)
    state = _ConsumerState(asyncio.Condition())
    consumer = asyncio.create_task(_consume_first_live(stream, state))
    handles: list[TurnHandle] = []
    tracing = False
    try:
        await asyncio.wait_for(fixture.entered.wait(), timeout=_DEADLINE_SECONDS)
        handles.extend(await _start_held_turns(fixture, held, state))
        gc.collect()
        tracemalloc.start()
        tracing = True
        baseline = tracemalloc.get_traced_memory()[0]
        first = await _fill_live_slots(fixture, held, handles, state, stream, consumer)
        gc.collect()
        full_bytes = tracemalloc.get_traced_memory()[0]
        primary = held[0]
        await _run_overflow_control(fixture, primary, handles)
        drained = await _drain_live_buffer(stream, first, fixture.model)
        if len(drained) != _HELD_TURNS + 1:
            raise RuntimeError("benchmark_live_buffer_count_mismatch")
        if len({event.session_id for event in drained}) != _HELD_TURNS:
            raise RuntimeError("benchmark_live_buffer_identity_mismatch")
        distinct_attempts = len({event.attempt_id for event in drained})
        if distinct_attempts != _HELD_TURNS + 1:
            raise RuntimeError("benchmark_live_buffer_attempt_identity_mismatch")
        payload = max(len(canonical(event.model_dump(mode="json"))) for event in drained)
        await _finish_live_models(fixture, held, handles)
        return {
            "baseline_current_bytes": baseline,
            "full_retained_current_bytes": full_bytes,
            "python_growth_bytes": _positive_growth(full_bytes, baseline),
            "live_buffer_capacity": _QUEUE_SIZE,
            "live_buffer_occupancy": _QUEUE_SIZE,
            "distinct_attempts": distinct_attempts,
            "live_event_max_serialized_bytes": payload,
            "live_event_max_text_bytes": max(len(event.text) for event in drained),
            "overflow_control_missed": 1,
        }
    finally:
        await run_retained(_cleanup_live_buffer(fixture, stream, consumer, handles, tracing))


async def _run_overflow_control(fixture: _Fixture, primary: str, handles: list[TurnHandle]) -> int:
    fixture.model.release_attempt(primary, 2)
    await _finished(handles[-1], "b" * _TEXT_BYTES)
    ordinal = fixture.model.register_primary_retry("primary_overflow")
    handles.append(await fixture.client.send(primary, "fabricated overflow", spec="benchmark"))
    await fixture.model.wait_started(primary, ordinal)
    fixture.model.emit_primary_overflow.set()
    await asyncio.wait_for(
        fixture.model.wait_published(primary, ordinal), timeout=_DEADLINE_SECONDS
    )
    return ordinal


async def _cleanup_live_buffer(
    fixture: _Fixture,
    stream: AsyncIterator[Change],
    consumer: asyncio.Task[ReplyText],
    handles: Sequence[TurnHandle],
    tracing: bool,
) -> None:
    failures: list[BaseException] = []
    fixture.model.emit_primary_first.set()
    fixture.model.emit_primary_second.set()
    fixture.model.emit_others.set()
    fixture.model.emit_primary_overflow.set()
    for event in fixture.model.release.values():
        event.set()
    try:
        await cancel_and_join([consumer])
    except BaseException as error:
        failures.append(error)
    try:
        await _close_stream(stream)
    except BaseException as error:
        failures.append(error)
    for handle in handles:
        try:
            await handle.result()
        except BaseException as error:
            failures.append(error)
    if tracing:
        tracemalloc.stop()
    if failures:
        raise failures[0]


async def _finish_live_models(
    fixture: _Fixture, held: Sequence[str], handles: Sequence[TurnHandle]
) -> None:
    for event in fixture.model.release.values():
        event.set()
    results = await asyncio.gather(*(handle.result() for handle in handles))
    expected = ["a" * _TEXT_BYTES, *(["x" * _TEXT_BYTES] * (len(held) - 1))]
    expected.extend(("b" * _TEXT_BYTES, "c" * _TEXT_BYTES))
    if len(results) != len(expected) or any(
        result.stop_reason != "final"
        or not result.messages
        or result.messages[-1].content != answer
        for result, answer in zip(results, expected, strict=True)
    ):
        raise RuntimeError("benchmark_live_model_cleanup_incomplete")


async def _trial() -> dict[str, dict[str, int]]:
    persisted_fixture = await _fixture(pause_changes=True, pause_after_first_change=True)
    try:
        control = await persisted_fixture.client.create(ref="control", spec="benchmark")
        persisted = await _fill_persisted_queue(persisted_fixture, control.id)
    finally:
        await _close_fixture(persisted_fixture)
    live_fixture = await _fixture()
    try:
        held = []
        for index in range(_HELD_TURNS):
            summary = await live_fixture.client.create(ref=f"live-{index}", spec="benchmark")
            held.append(summary.id)
        live = await _fill_live_buffer(live_fixture, held)
    finally:
        await _close_fixture(live_fixture)
    coalesced = await _coalesced_buffer_trial()
    return {"persisted": persisted, "live": live, "coalesced": coalesced}
