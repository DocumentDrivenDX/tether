"""Provisional text requires a started physical identity and current READ_SESSION."""

import asyncio
from contextlib import aclosing
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import JsonValue

from tether.engine.attempts import JournalAttemptRecorder
from tether.engine.governance import Governance
from tether.sessions.feed import LiveBuffer
from tether.types import AttemptIdentity, ModelAttemptDenied, ModelResponse, TextDelta
from tether.types.canonical import digest
from tether.types.client import Lagged, ReplyReset, ReplyText
from tether.types.context import ContextError
from tether.types.execution import Actor, Answer

pytest_plugins = ("test_client_reads",)
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_only_started_attempt_deltas_are_disclosed(harness):
    from tether.models.testing.scripted import prepare_fixture

    h, events = harness, []

    async def disclose(owner, identity, offset, text):
        events.append((identity.attempt_id, offset, text))

    class ForgedAttemptModel:
        provider = "forged"

        async def stream(self, request):
            identity = await prepare_fixture(request)
            assert identity is not None
            claim = identity.model_copy(update={"attempt_id": "fabricated-unstarted"})
            yield TextDelta(text="A", identity=claim)
            yield ModelResponse(content="A", finish_reason="stop")

    h.engine.live_text = disclose
    h.engine.model = ForgedAttemptModel()
    session = await h.client.create(ref="second", title="Second", spec="demo")
    handle = await h.client.send(session.id, "new request", spec="demo")
    result = await handle._task
    assert result.stop_reason == "model_denied"
    assert events == []


async def test_failed_final_admission_does_not_mark_attempt_started(model_request):
    tags = model_request.tags
    events = []

    async def authorize(*args, **kwargs):
        return None

    async def fact(*args, **kwargs):
        return None

    async def disclose(owner, identity, offset, text):
        events.append((identity, offset, text))

    def fail_admission():
        raise ContextError("context_invalid")

    gov = cast(
        Governance,
        SimpleNamespace(
            actor=Actor(id=tags.principal),
            log=SimpleNamespace(session=SimpleNamespace(id=tags.session_id)),
            observers=SimpleNamespace(tracer=SimpleNamespace(current_ids=lambda: None)),
            authorize=authorize,
            fact=fact,
        ),
    )
    recorder = JournalAttemptRecorder(gov, tags, "demo", admit=fail_admission, live_text=disclose)
    identity = await recorder.reserve()
    wire: JsonValue = {"request": "bounded"}
    with pytest.raises(ContextError, match="context_invalid"):
        await recorder.start(identity, request_digest=digest(wire), wire_request=wire)
    with pytest.raises(ModelAttemptDenied, match="invalid_attempt_identity"):
        await recorder.disclose_delta(identity, "unaccepted")
    assert events == []


async def test_current_read_policy_is_rechecked_before_each_delta(harness):
    h = harness
    events = []

    async def disclose(owner, identity, offset, text):
        events.append((identity.attempt_id, offset, text))
        if len(events) == 1:
            h.policy.read = False

    h.engine._runs[h.session.id].live_text = disclose
    h.model._script = (
        TextDelta(text="A"),
        TextDelta(text="B"),
        ModelResponse(content="AB", finish_reason="stop"),
    )
    h.model._position = 0
    pending = await h.client.pending(h.session.id)
    handle = await h.client.confirm(h.session.id, pending.invocation_id, Answer(approved=True))
    assert handle is not None
    result = await handle._task
    assert result.stop_reason == "model_denied"
    assert len(events) == 1
    assert events[0][1:] == (0, "A")


async def test_started_deltas_are_delivered_with_contiguous_offsets(harness):
    h = harness
    events = []

    async def disclose(owner, identity, offset, text):
        events.append((identity.attempt_id, offset, text))

    h.engine._runs[h.session.id].live_text = disclose
    h.model._script = (
        TextDelta(text="A"),
        TextDelta(text="B"),
        ModelResponse(content="AB", finish_reason="stop"),
    )
    h.model._position = 0
    pending = await h.client.pending(h.session.id)
    handle = await h.client.confirm(h.session.id, pending.invocation_id, Answer(approved=True))
    assert handle is not None
    result = await handle._task
    assert result.stop_reason == "final"
    assert len(events) == 2
    assert events[0][0] == events[1][0]
    assert [(offset, text) for _, offset, text in events] == [(0, "A"), (1, "B")]


async def test_live_follower_reconnect_invalidates_old_attempt(harness):
    from tether.models.testing.scripted import observe_fixture, prepare_fixture

    h = harness

    class PausedModel:
        provider = "paused"

        def __init__(self):
            self.first = asyncio.Event()
            self.release = asyncio.Event()

        async def stream(self, request):
            identity = await prepare_fixture(request)
            assert identity is not None
            yield TextDelta(text="A", identity=identity)
            self.first.set()
            await self.release.wait()
            yield TextDelta(text="B", identity=identity)
            response = ModelResponse(content="AB", finish_reason="stop")
            await observe_fixture(request, identity, response, self.provider)
            yield response

    model = PausedModel()
    h.engine.model = model
    session = await h.client.create(ref="live", title="Live", spec="demo")
    before = await h.client.snapshot(session.id)
    first_stream = h.client.follow(session_id=session.id, since=before.seq)
    first_reader = asyncio.create_task(anext(first_stream))
    for _ in range(100):
        if h.app.followers.live:
            break
        await asyncio.sleep(0)
    handle = await h.client.send(session.id, "live request", spec="demo")
    await model.first.wait()
    assert h.app.followers.active
    first = await asyncio.wait_for(first_reader, 2)
    while not isinstance(first, ReplyText):
        first = await asyncio.wait_for(anext(first_stream), 2)
    assert first.text == "A"
    assert first.offset == 0

    second_stream = h.client.follow(session_id=session.id, since=before.seq)
    second_reader = asyncio.create_task(anext(second_stream))
    await asyncio.sleep(0)
    second_events = []
    second_events.append(await asyncio.wait_for(second_reader, 2))
    assert isinstance(second_events[0], ReplyReset)
    while not any(isinstance(event, ReplyReset) for event in second_events):
        second_events.append(await asyncio.wait_for(anext(second_stream), 2))
    reset = next(event for event in second_events if isinstance(event, ReplyReset))
    assert reset.reason == "reconnect"
    assert reset.attempt_id == first.attempt_id

    model.release.set()
    result = await handle._task
    assert result.stop_reason == "final"
    assert not h.app.followers.active
    await aclosing(first_stream).__aexit__(None, None, None)
    await aclosing(second_stream).__aexit__(None, None, None)


async def test_live_buffer_coalesces_contiguous_text_and_marks_loss():
    buffer = LiveBuffer(1, 4096)
    identity = AttemptIdentity(
        session_id="s", turn_id="t", model_call_id="m", attempt_id="a", purpose="answer"
    )
    first = ReplyText(session_id="s", turn_id="t", attempt_id="a", offset=0, text="a")
    buffer.put(first)
    buffer.put(first.model_copy(update={"offset": 1, "text": "b"}))
    assert await buffer.get() == first.model_copy(update={"text": "ab"})

    buffer.put(first)
    buffer.put(first.model_copy(update={"attempt_id": "b"}))
    assert await buffer.get() == first
    assert await buffer.get() == Lagged(missed=1)
    buffer.reset(identity, "reconnect")
    assert await buffer.get() == ReplyReset(
        session_id="s", turn_id="t", attempt_id="a", reason="reconnect"
    )


async def test_live_buffer_preserves_final_deltas_and_reports_dropped_reset():
    buffer = LiveBuffer(1, 4096)
    identity = AttemptIdentity(
        session_id="s", turn_id="t", model_call_id="m", attempt_id="a", purpose="answer"
    )
    text = ReplyText(session_id="s", turn_id="t", attempt_id="a", offset=0, text="kept")
    buffer.put(text)
    buffer.finish(identity, None)
    assert await buffer.get() == text

    buffer.reset(identity, "reconnect")
    next_identity = identity.model_copy(update={"attempt_id": "b"})
    buffer.reset(next_identity, "reconnect")
    assert await buffer.get() == Lagged(missed=1)
    assert await buffer.get() == ReplyReset(
        session_id="s", turn_id="t", attempt_id="b", reason="reconnect"
    )
