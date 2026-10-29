"""The reference UI keeps provisional replies separate from committed history."""

import asyncio

import pytest
from textual.widgets import Input, RichLog, Static

from tether.models.testing.scripted import observe_fixture, prepare_fixture
from tether.tui import DemoApp
from tether.types import ModelResponse, ModelUnavailable, TextDelta
from tether.types.client import ReplyText

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


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


async def wait_reply(pilot, reply, expected):
    async with asyncio.timeout(5):
        while reply.content != expected:
            await pilot.pause()


@pytest.mark.parametrize("fail", [False, True])
async def test_real_client_stream_updates_one_widget_and_clears_at_terminal(tmp_path, fail):
    app = DemoApp(tmp_path, volatile=True)
    model = PausedReply(fail=fail)
    async with asyncio.timeout(20), app.run_test(size=(110, 30)) as pilot:
        client = app.client
        assert client is not None
        assert app.session_id is not None
        client._service.engine.model = model
        app.query_one("#message", Input).value = "Fabricated request"
        sending = asyncio.create_task(app._send())
        reply = app.query_one("#reply", Static)
        try:
            await wait_reply(pilot, reply, "Hello  ▍")
            assert reply.display
            model.next_fragment.set()
            await wait_reply(pilot, reply, "Hello world. ▍")
            assert app.query_one("#reply", Static) is reply
            before = [str(line) for line in app.query_one("#transcript", RichLog).lines]
            assert not any("live:" in line or "Hello" in line for line in before)
        finally:
            model.next_fragment.set()
            model.finish.set()
            await sending
        await pilot.pause()
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


async def test_reconnect_clears_the_current_reply_without_persisting_it(tmp_path):
    app = DemoApp(tmp_path, volatile=True)
    model = PausedReply()
    async with asyncio.timeout(20), app.run_test() as pilot:
        assert app.client is not None
        app.client._service.engine.model = model
        app.query_one("#message", Input).value = "Fabricated request"
        sending = asyncio.create_task(app._send())
        reply = app.query_one("#reply", Static)
        try:
            await wait_reply(pilot, reply, "Hello  ▍")
            await app._stop_follower()
            app._follower = asyncio.create_task(app._follow())
            await wait_reply(pilot, reply, "")
            assert not reply.display
        finally:
            model.next_fragment.set()
            model.finish.set()
            await sending


async def test_foreign_gap_and_session_switch_never_merge_partial_replies(tmp_path):
    app = DemoApp(tmp_path, volatile=True)
    async with asyncio.timeout(20), app.run_test() as pilot:
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
        await pilot.pause()
        assert not reply.display
        app._update_reply(first.model_copy(update={"attempt_id": "second", "offset": 6}))
        assert not reply.display
