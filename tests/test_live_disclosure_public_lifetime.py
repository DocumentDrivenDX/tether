"""Public Client lifetime checks for sealed live-attempt publishers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import Any, cast

import pytest

from tether.types import ModelResponse, TextDelta
from tether.types.client import ReplyText
from tether.types.execution import Answer

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _wait_for_follower(harness, key: tuple[str, str | None], monkeypatch) -> None:
    ready = asyncio.Event()
    followers = harness.app.followers
    original = followers._subscribe

    def subscribe(owner, session_id, buffer):
        original(owner, session_id, buffer)
        if (owner, session_id) == key:
            ready.set()

    monkeypatch.setattr(followers, "_subscribe", subscribe)
    if key not in followers.live:
        async with asyncio.timeout(2):
            await ready.wait()


async def _finish_reply(client, session_id: str, content: str):
    handle = await client.send(session_id, "Fabricated repeated request", spec="demo")
    result = await asyncio.wait_for(handle.result(), 2)
    assert result.stop_reason == "final"
    assert result.messages[-1].content == content
    return handle


class _OwnerFollower:
    def __init__(self, harness: Any, monkeypatch: Any) -> None:
        self.h, self.monkeypatch = harness, monkeypatch
        self.events: list[object] = []
        self.replies: dict[str, str] = {}
        self.waiters: dict[str, asyncio.Future[None]] = {}
        self.stream: AsyncGenerator[Any, None] | None = None
        self.consumer: asyncio.Task[None] | None = None

    async def __aenter__(self):
        h = self.h
        initial = await h.client.pending(h.session.id)
        assert initial is not None
        first = await h.client.confirm(h.session.id, initial.invocation_id, Answer(approved=True))
        assert first is not None
        assert (await first.result()).stop_reason == "final"
        h.app.access.limits = h.app.access.limits.model_copy(update={"follower_queue_size": 2})
        baseline = await h.client.snapshot(h.session.id)
        self.stream = cast(AsyncGenerator[Any, None], h.client.follow(since=baseline.seq))
        self.consumer = asyncio.create_task(self._consume())
        await _wait_for_follower(h, (h.actor.id, None), self.monkeypatch)
        return self

    async def _consume(self) -> None:
        assert self.stream is not None
        async for event in self.stream:
            self.events.append(event)
            if not isinstance(event, ReplyText):
                continue
            self.replies[event.turn_id] = self.replies.get(event.turn_id, "") + event.text
            waiter = self.waiters.get(event.turn_id)
            if waiter is not None and not waiter.done():
                waiter.set_result(None)

    async def run_round(self, index: int) -> None:
        h = self.h
        content = f"Fabricated reply {index}"
        session = await h.client.create(
            ref=f"lifetime-{index}", spec="demo", title="Fabricated lifetime"
        )
        h.model._script = (ModelResponse(content=content, finish_reason="stop"),)
        h.model._position = 0
        handle = await _finish_reply(h.client, session.id, content)
        if handle.turn_id not in self.replies:
            waiter = asyncio.get_running_loop().create_future()
            self.waiters[handle.turn_id] = waiter
            if handle.turn_id in self.replies:
                waiter.set_result(None)
            await asyncio.wait_for(waiter, 2)
            self.waiters.pop(handle.turn_id, None)
        assert self.replies[handle.turn_id] == content
        assert not h.app.followers.active
        buffers = h.app.followers.live[(h.actor.id, None)]
        assert len(buffers) == 1
        buffer = next(iter(buffers))
        assert not buffer.blocked
        assert not buffer.closed_attempts

    async def __aexit__(self, *exc: object) -> None:
        assert self.consumer is not None
        self.consumer.cancel()
        await asyncio.gather(self.consumer, return_exceptions=True)
        if self.stream is not None:
            await aclosing(self.stream).__aexit__(*exc)
        if not exc[0]:
            assert sum(isinstance(event, ReplyText) for event in self.events) == 8


class _ContinuationProbe:
    def __init__(self, harness: Any, monkeypatch: Any) -> None:
        self.h, self.monkeypatch = harness, monkeypatch
        self.auth_started, self.release_auth = asyncio.Event(), asyncio.Event()
        self.new_delta_ready, self.release_model = asyncio.Event(), asyncio.Event()
        self.received: list[ReplyText] = []
        self.reply_received = asyncio.get_running_loop().create_future()
        self.late: asyncio.Task[None] | None = None
        self.consumer: asyncio.Task[None] | None = None
        self.stream: AsyncGenerator[Any, None] | None = None
        self.run_state = None
        self.old_recorder = None

    async def __aenter__(self):
        h = self.h
        baseline = await h.client.snapshot(h.session.id)
        self.stream = cast(
            AsyncGenerator[Any, None],
            h.client.follow(session_id=h.session.id, since=baseline.seq),
        )
        self.consumer = asyncio.create_task(self._consume())
        await _wait_for_follower(h, (h.actor.id, h.session.id), self.monkeypatch)
        return self

    async def _consume(self) -> None:
        assert self.stream is not None
        async for event in self.stream:
            if isinstance(event, ReplyText):
                self.received.append(event)
                if not self.reply_received.done():
                    self.reply_received.set_result(None)

    async def _delay_old_callback(self, old_recorder, old_identity) -> None:
        h = self.h
        original_authorize = h.policy.authorize
        original_stream = h.model.stream
        block_next_read = True

        async def authorize(request):
            nonlocal block_next_read
            if request.action == "read_session" and block_next_read:
                block_next_read = False
                self.auth_started.set()
                await self.release_auth.wait()
            return await original_authorize(request)

        async def stream(request):
            async for item in original_stream(request):
                yield item
                if isinstance(item, TextDelta):
                    self.new_delta_ready.set()
                    await self.release_model.wait()

        h.policy.authorize = authorize
        h.model.stream = stream
        self.late = asyncio.create_task(
            old_recorder.disclose_delta(old_identity, "stale old phase")
        )
        await asyncio.wait_for(self.auth_started.wait(), 2)

    async def run(self) -> None:
        h = self.h
        session_id = h.session.id
        run = h.engine._runs[session_id]
        self.run_state = run
        old_recorder = run.recorder
        assert old_recorder is not None
        assert old_recorder.live_text is not None
        self.old_recorder = old_recorder
        old_identity = next(iter(old_recorder.started.values()))[0]
        await self._delay_old_callback(old_recorder, old_identity)
        pending = await h.client.pending(session_id)
        assert pending is not None
        handle = await h.client.confirm(session_id, pending.invocation_id, Answer(approved=True))
        assert handle is not None
        await asyncio.wait_for(self.new_delta_ready.wait(), 2)
        self.release_auth.set()
        late = self.late
        assert late is not None
        await asyncio.wait_for(late, 2)
        self.release_model.set()
        result = await asyncio.wait_for(handle.result(), 2)
        assert result.stop_reason == "final"
        assert result.messages[-1].content == "Fabricated done"
        assert run.recorder is not None
        assert run.recorder is not old_recorder
        assert run.recorder.live_text is not None
        await asyncio.wait_for(self.reply_received, 2)
        assert [(event.text, event.offset) for event in self.received] == [("Fabricated done", 0)]
        assert not h.app.followers.active
        buffer = next(iter(h.app.followers.live[(h.actor.id, session_id)]))
        assert not buffer.blocked
        assert not buffer.closed_attempts

    async def __aexit__(self, *exc: object) -> None:
        self.release_auth.set()
        self.release_model.set()
        if self.late is not None:
            await asyncio.gather(self.late, return_exceptions=True)
        if self.consumer is not None:
            self.consumer.cancel()
            await asyncio.gather(self.consumer, return_exceptions=True)
        if self.stream is not None:
            await aclosing(self.stream).__aexit__(*exc)


async def test_public_owner_follower_retires_markers_after_more_than_capacity(harness, monkeypatch):
    async with _OwnerFollower(harness, monkeypatch) as follower:
        for index in range(8):
            await follower.run_round(index)


async def test_public_continuation_seals_the_previous_recorder_callback(harness, monkeypatch):
    async with _ContinuationProbe(harness, monkeypatch) as probe:
        await probe.run()
