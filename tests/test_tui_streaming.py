"""The reference UI keeps provisional replies separate from committed history."""

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest
from test_client_reads import harness as harness
from textual.widgets import Input, ListView, RichLog, Static
from tui_event_waits import (
    let_pilot_screen_wait_use_outer_owner,
    wait_for_event_or_operation,
    wait_for_event_or_operations,
)

from tether.client import Client
from tether.models.testing.scripted import observe_fixture, prepare_fixture
from tether.tui import ClientApp, DemoApp
from tether.types import ModelResponse, ModelUnavailable, TextDelta
from tether.types.client import ReplyText

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def pilot_screen_watchdog(monkeypatch):
    let_pilot_screen_wait_use_outer_owner(monkeypatch)


class PausedReply:
    provider = "fabricated-stream"

    def __init__(self, *, fail=False):
        self.next_fragment = asyncio.Event()
        self.finish = asyncio.Event()
        self.fail = fail
        self.identity = None

    async def stream(self, request):
        self.identity = await prepare_fixture(request)
        yield TextDelta(text="Hello ", identity=self.identity)
        await self.next_fragment.wait()
        yield TextDelta(text="world.", identity=self.identity)
        await self.finish.wait()
        if self.fail:
            raise ModelUnavailable("fabricated outage")
        response = ModelResponse(content="Hello world.", finish_reason="stop")
        await observe_fixture(request, self.identity, response, self.provider)
        yield response


class ObservedDemoApp(DemoApp):
    def __init__(self, root: Path, *, volatile: bool = False) -> None:
        self.reply_changed = asyncio.Event()
        super().__init__(root, volatile=volatile)

    def _update_reply(self, change: ReplyText) -> None:
        super()._update_reply(change)
        self.reply_changed.set()

    def _reset_reply(self) -> None:
        super()._reset_reply()
        self.reply_changed.set()


async def wait_reply(
    pilot, app: ObservedDemoApp, reply: Static, expected: str, operation: asyncio.Task[None]
) -> None:
    follower = app._follower
    assert follower is not None
    producer_completed = False
    while reply.content != expected:
        if not producer_completed and operation.done():
            operation.result()
            producer_completed = True
        app.reply_changed.clear()
        if reply.content == expected:
            return
        operations: list[asyncio.Task[None]] = [follower]
        if not producer_completed:
            operations.append(operation)
        state_seen, completed = await wait_for_event_or_operations(app.reply_changed, *operations)
        for ended in completed:
            await ended
        if state_seen:
            continue
        if follower in completed:
            raise AssertionError("reply_consumer_finished_before_expected_update")
        if operation in completed:
            operation.result()
            producer_completed = True
            continue
        await pilot._wait_for_screen()


@pytest.mark.parametrize("fail", [False, True])
async def test_real_client_stream_updates_one_widget_and_clears_at_terminal(tmp_path, fail):
    app = ObservedDemoApp(tmp_path, volatile=True)
    model = PausedReply(fail=fail)
    async with app.run_test(size=(110, 30)) as pilot:
        client = app.client
        assert client is not None
        assert app.session_id is not None
        cast(Client, client)._service.engine.model = model
        app.query_one("#message", Input).value = "Fabricated request"
        sending = asyncio.create_task(app._send())
        reply = app.query_one("#reply", Static)
        try:
            await wait_reply(pilot, app, reply, "Hello  ▍", sending)
            assert reply.display
            model.next_fragment.set()
            await wait_reply(pilot, app, reply, "Hello world. ▍", sending)
            assert app.query_one("#reply", Static) is reply
            before = [str(line) for line in app.query_one("#transcript", RichLog).lines]
            assert not any("live:" in line or "Hello" in line for line in before)
        finally:
            model.next_fragment.set()
            model.finish.set()
            await sending
        await pilot._wait_for_screen()
        assert not reply.display
        assert reply.content == ""
        lines = [str(line) for line in app.query_one("#transcript", RichLog).lines]
        assert sum("assistant: Hello world." in line for line in lines) == (0 if fail else 1)
        assert not any("live:" in line for line in lines)
        assert model.identity is not None
        app._update_reply(
            ReplyText(
                session_id=app.session_id,
                turn_id=model.identity.turn_id,
                attempt_id=model.identity.attempt_id,
                offset=0,
                text="Stale",
            )
        )
        assert not reply.display


async def test_reply_wait_keeps_waiting_for_consumer_after_producer_success():
    state = asyncio.Event()
    release = asyncio.Event()
    consumer_started = asyncio.Event()

    async def finished_producer():
        return None

    async def delayed_consumer():
        consumer_started.set()
        await release.wait()
        state.set()

    producer = asyncio.create_task(finished_producer())
    consumer = asyncio.create_task(delayed_consumer())
    try:
        await producer
        await wait_for_event_or_operation(consumer_started, consumer)
        state_seen, completed = await wait_for_event_or_operations(state, producer, consumer)
        assert not state_seen
        assert completed == (producer,)
        release.set()
        state_seen, completed = await wait_for_event_or_operations(state, consumer)
        assert state_seen
        assert completed == (consumer,)
        await consumer
    finally:
        release.set()
        for task in (producer, consumer):
            if not task.done():
                task.cancel()
        await asyncio.gather(producer, consumer, return_exceptions=True)


async def test_wait_reply_surfaces_producer_failure_before_waiting_for_consumer():
    from types import SimpleNamespace

    async def failed_producer():
        raise RuntimeError("fabricated producer failure")

    producer = asyncio.create_task(failed_producer())
    follower = asyncio.create_task(asyncio.Event().wait())
    fake_app = cast(
        ObservedDemoApp, SimpleNamespace(reply_changed=asyncio.Event(), _follower=follower)
    )
    try:
        await asyncio.gather(producer, return_exceptions=True)
        with pytest.raises(RuntimeError, match="fabricated producer failure"):
            await wait_reply(cast(Any, object()), fake_app, Static("current"), "expected", producer)
    finally:
        if not follower.done():
            follower.cancel()
        await asyncio.gather(producer, follower, return_exceptions=True)


async def test_reconnect_clears_the_current_reply_without_persisting_it(tmp_path):
    app = ObservedDemoApp(tmp_path, volatile=True)
    model = PausedReply()
    async with app.run_test() as pilot:
        assert app.client is not None
        cast(Client, app.client)._service.engine.model = model
        app.query_one("#message", Input).value = "Fabricated request"
        sending = asyncio.create_task(app._send())
        reply = app.query_one("#reply", Static)
        try:
            await wait_reply(pilot, app, reply, "Hello  ▍", sending)
            await app._stop_follower()
            app._start_follower(None)
            await wait_reply(pilot, app, reply, "", sending)
            assert not reply.display
        finally:
            model.next_fragment.set()
            model.finish.set()
            await sending


async def test_inflight_session_refresh_finishes_after_list_widget_is_unmounted(
    tmp_path, monkeypatch
):
    app = DemoApp(tmp_path, volatile=True)
    entered, release = asyncio.Event(), asyncio.Event()
    async with asyncio.timeout(20), app.run_test():
        await app._stop_follower()
        assert app.client is not None
        original = app.client.list

        async def delayed_list():
            page = await original()
            entered.set()
            await release.wait()
            return page

        monkeypatch.setattr(app.client, "list", delayed_list)
        refresh = asyncio.create_task(app._refresh_sessions())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await app.query_one("#sessions", ListView).remove()
        finally:
            release.set()
            await refresh
        assert app.sessions


async def test_foreign_gap_and_session_switch_never_merge_partial_replies(tmp_path):
    app = DemoApp(tmp_path, volatile=True)
    async with app.run_test() as pilot:
        reply = app.query_one("#reply", Static)
        assert app.session_id is not None
        app._reply_turn = "fabricated-turn"
        first = ReplyText(
            session_id=app.session_id,
            turn_id="fabricated-turn",
            attempt_id="first",
            offset=0,
            text="First",
        )
        app._update_reply(first)
        app._update_reply(first.model_copy(update={"session_id": "other", "text": "Foreign"}))
        assert reply.content == "First ▍"
        app._update_reply(first.model_copy(update={"offset": 99, "text": "Gap"}))
        assert not reply.display
        app._update_reply(first.model_copy(update={"attempt_id": "second", "text": "Second"}))
        assert reply.content == "Second ▍"
        await app._show_session()
        await pilot._wait_for_screen()
        assert not reply.display
        app._update_reply(first.model_copy(update={"attempt_id": "second", "offset": 6}))
        assert not reply.display


async def test_client_app_borrows_injected_client(harness):
    app = ClientApp(harness.client, session_id=harness.session.id)
    async with app.run_test():
        assert app.client is harness.client
        assert app.session_id == harness.session.id
    assert (await harness.client.get(harness.session.id)).summary.id == harness.session.id
