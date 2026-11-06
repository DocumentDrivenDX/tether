"""Observe loop lag while real subscribers consume one fabricated streaming turn."""

from __future__ import annotations

import asyncio
import math
import statistics
import time
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import replace

from selected_profiles_process import await_retained

from tether.client import (
    Actor,
    ClientHost,
    ClientSettings,
    EntriesAppended,
    Lagged,
    ModelRequest,
    ModelResponse,
    ReplyReset,
    ReplyText,
    ResyncRequired,
    StreamItem,
    TextDelta,
)
from tether.client.testing import scripted_model


class PacedModel:
    provider = "scripted-paced-benchmark"

    def __init__(self, samples: int) -> None:
        self.streaming = asyncio.Event()
        self.chunks = 200
        self.delegate = scripted_model(
            [TextDelta(text="x") for _ in range(self.chunks)]
            + [ModelResponse(content="x" * self.chunks, finish_reason="stop")]
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamItem]:
        self.streaming.set()
        try:
            async for item in self.delegate.stream(request):
                if isinstance(item, TextDelta):
                    await asyncio.sleep(0.01)
                yield item
        finally:
            self.streaming.clear()


async def streaming_followers(
    settings: ClientSettings, count: int, samples: int
) -> dict[str, object]:
    model = PacedModel(samples)
    config = replace(
        settings,
        model=model,
        model_limits=settings.model_limits.model_copy(update={"request_timeout_seconds": 120}),
    )
    async with await ClientHost.open(config) as host:
        client = host.client(Actor(id="fabricated-streaming-benchmark"))
        session = await client.create(spec="demo")
        baseline = await client.snapshot(session.id)
        ready: asyncio.Queue[None] = asyncio.Queue(maxsize=count)
        readers = [
            asyncio.create_task(
                _consume_streaming_turn(
                    client.follow(session_id=session.id, since=baseline.seq), ready
                )
            )
            for _ in range(count)
        ]
        watcher: asyncio.Task[list[float]] | None = None
        try:
            # The in-process feed has no idle heartbeat. A real persisted update
            # makes registration observable, including readers that replay it.
            await client.update(session.id, title="Fabricated registration barrier")
            for _ in range(count):
                await asyncio.wait_for(ready.get(), timeout=30)
            watcher = asyncio.create_task(observe_stream(model))
            result = await asyncio.wait_for(
                (
                    await client.send(session.id, "Fabricated streaming workload", spec="demo")
                ).result(),
                timeout=150,
            )
            values = await watcher
            consumers = await asyncio.wait_for(asyncio.gather(*readers), timeout=30)
            if result.stop_reason != "final" or len(values) < samples:
                raise RuntimeError("streaming_follower_observations_incomplete")
            if any(turn_id != result.turn_id or text != "x" * 200 for turn_id, text in consumers):
                raise RuntimeError("streaming_follower_reply_mismatch")
            ordered = sorted(values)
            return {
                "followers": count,
                "queue_size": config.client_limits.follower_queue_size,
                "stream_chunks": model.chunks,
                "observations": len(values),
                "consumers_verified": len(consumers),
                "terminal_boundaries": len(consumers),
                "exact_ordered_reply_consumers": len(consumers),
                "missed_events": 0,
                "event_loop_lag": {
                    "p99_ms": ordered[math.ceil(len(ordered) * 0.99) - 1],
                    "median_ms": statistics.median(values),
                },
                "scope": "One paced fabricated streaming turn with registered consuming followers",
            }
        finally:
            for task in [*readers, *([watcher] if watcher is not None else [])]:
                task.cancel()
            await asyncio.gather(
                *readers, *([watcher] if watcher is not None else []), return_exceptions=True
            )


async def _consume_streaming_turn(
    events: AsyncIterator[object], ready: asyncio.Queue[None]
) -> tuple[str, str]:
    started = False
    reply = _ReplyAccumulator()
    try:
        async for event in events:
            if not started:
                ready.put_nowait(None)
                started = True
            if isinstance(event, Lagged):
                if event.missed > 0:
                    raise RuntimeError("streaming_follower_missed_events")
            elif isinstance(event, (ResyncRequired, ReplyReset)):
                code = (
                    "streaming_follower_resync_required"
                    if isinstance(event, ResyncRequired)
                    else "streaming_follower_reply_reset"
                )
                raise RuntimeError(code)
            elif isinstance(event, ReplyText):
                reply.append(event)
            elif isinstance(event, EntriesAppended) and reply.is_terminal(event):
                return reply.finish(event)
        raise RuntimeError("streaming_follower_terminal_boundary_missing")
    finally:
        if isinstance(events, AsyncGenerator):
            await await_retained(asyncio.create_task(events.aclose()))


class _ReplyAccumulator:
    def __init__(self) -> None:
        self.turn_id: str | None = None
        self.attempt_id: str | None = None
        self.text_parts: list[str] = []
        self.next_offset = 0
        self.reply_events = 0

    def append(self, event: ReplyText) -> None:
        if self.turn_id is None:
            self.turn_id, self.attempt_id = event.turn_id, event.attempt_id
        if event.turn_id != self.turn_id or event.attempt_id != self.attempt_id:
            raise RuntimeError("streaming_follower_reply_identity_changed")
        if event.offset != self.next_offset:
            raise RuntimeError("streaming_follower_reply_offset_gap")
        self.text_parts.append(event.text)
        self.next_offset += len(event.text)
        self.reply_events += 1

    def is_terminal(self, event: EntriesAppended) -> bool:
        return any(
            entry.kind == "turn_ended" and entry.turn_id == self.turn_id for entry in event.entries
        )

    def finish(self, event: EntriesAppended) -> tuple[str, str]:
        if not event.last_in_commit or not self.reply_events or self.turn_id is None:
            raise RuntimeError("streaming_follower_terminal_boundary_invalid")
        return self.turn_id, "".join(self.text_parts)


async def observe_stream(model: PacedModel) -> list[float]:
    await asyncio.wait_for(model.streaming.wait(), timeout=30)
    values = []
    while model.streaming.is_set():
        before = time.perf_counter()
        await asyncio.sleep(0.01)
        if model.streaming.is_set():
            values.append(max(0, time.perf_counter() - before - 0.01) * 1000)
    return values
