"""Provisional text requires a started physical identity and current READ_SESSION."""

import asyncio
from contextlib import aclosing
from types import MethodType, SimpleNamespace
from typing import cast

import pytest
from pydantic import JsonValue

from tether.engine.attempts import JournalAttemptRecorder
from tether.engine.governance import Governance
from tether.engine.run import Run, _LiveDisclosureScope
from tether.sessions import feed as feed_module
from tether.sessions.access import SessionAccess
from tether.sessions.feed import Followers, LiveBuffer
from tether.types import AttemptIdentity, ModelAttemptDenied, ModelResponse, RequestTags, TextDelta
from tether.types.canonical import digest
from tether.types.client import Lagged, ReplyReset, ReplyText
from tether.types.context import ContextError
from tether.types.execution import Actor, Answer

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


async def test_terminal_follow_retirement_bounds_attempt_markers():
    buffer = LiveBuffer(2, 4096)
    followers = Followers(cast(SessionAccess, SimpleNamespace()))
    followers.live[("owner", "s")] = {buffer}

    for index in range(256):
        identity = AttemptIdentity(
            session_id="s",
            turn_id=f"turn-{index}",
            model_call_id=f"call-{index}",
            attempt_id=f"attempt-{index}",
            purpose="answer",
        )
        event = ReplyText(
            session_id="s",
            turn_id=identity.turn_id,
            attempt_id=identity.attempt_id,
            offset=0,
            text="x",
        )
        await followers.publish_live("owner", identity, 0, "x")
        followers.finish_live("owner", identity, None)

        assert await buffer.get() == event
        assert identity.attempt_id not in buffer.blocked
        assert identity.attempt_id not in buffer.closed_attempts
    assert not followers.active


async def test_retirement_follows_overflow_and_reconnect_reset():
    buffer = LiveBuffer(1, 4096)
    followers = Followers(cast(SessionAccess, SimpleNamespace()))
    followers.live[("owner", "s")] = {buffer}
    first = AttemptIdentity(
        session_id="s", turn_id="t1", model_call_id="c1", attempt_id="a1", purpose="answer"
    )
    overflow = AttemptIdentity(
        session_id="s", turn_id="t2", model_call_id="c2", attempt_id="a2", purpose="answer"
    )
    await followers.publish_live("owner", first, 0, "first")
    await followers.publish_live("owner", overflow, 0, "overflow")
    assert overflow.attempt_id in buffer.blocked

    followers.finish_live("owner", overflow, "outage")
    assert overflow.attempt_id not in buffer.blocked
    assert overflow.attempt_id not in buffer.closed_attempts
    assert await buffer.get() == Lagged(missed=2)
    assert await buffer.get() == ReplyReset(
        session_id="s", turn_id="t2", attempt_id="a2", reason="outage"
    )

    reconnect = first.model_copy(update={"attempt_id": "a3", "turn_id": "t3"})
    buffer.reset(reconnect, "reconnect")
    assert reconnect.attempt_id in buffer.blocked
    followers.finish_live("owner", reconnect, None)
    assert reconnect.attempt_id not in buffer.blocked
    assert reconnect.attempt_id not in buffer.closed_attempts
    assert await buffer.get() == ReplyReset(
        session_id="s", turn_id="t3", attempt_id="a3", reason="reconnect"
    )


def _fanout_fixture(count: int = 40):
    followers = Followers(cast(SessionAccess, SimpleNamespace()))
    buffers = [LiveBuffer(4, 4096) for _ in range(count)]
    followers.live[("owner", "s")] = set(buffers)
    identity = AttemptIdentity(
        session_id="s", turn_id="t", model_call_id="c", attempt_id="a", purpose="answer"
    )
    return followers, buffers, identity


async def test_live_fanout_rechecks_active_identity_after_batch_yield(monkeypatch):
    followers, buffers, identity = _fanout_fixture()
    original_sleep = asyncio.sleep
    yielded = False

    async def finish_at_yield(delay=0):
        nonlocal yielded
        assert delay == 0
        yielded = True
        followers.finish_live("owner", identity, None)
        await original_sleep(0)

    monkeypatch.setattr(feed_module.asyncio, "sleep", finish_at_yield)
    await followers.publish_live("owner", identity, 0, "partial")

    assert yielded
    assert not followers.active
    received = sum(any(isinstance(item, ReplyText) for item in buffer.values) for buffer in buffers)
    assert received == 32


async def test_live_fanout_stops_when_equal_identity_is_reregistered(monkeypatch):
    followers, buffers, identity = _fanout_fixture()
    original_sleep = asyncio.sleep
    yielded = False

    async def replace_active_generation(delay=0):
        nonlocal yielded
        assert delay == 0
        yielded = True
        key = ("owner", identity.session_id, identity.turn_id)
        followers.active[key][identity.attempt_id] = identity.model_copy(deep=True)
        await original_sleep(0)

    monkeypatch.setattr(feed_module.asyncio, "sleep", replace_active_generation)
    await followers.publish_live("owner", identity, 0, "partial")

    assert yielded
    received = sum(any(isinstance(item, ReplyText) for item in buffer.values) for buffer in buffers)
    assert received == 32


async def test_live_fanout_skips_unsubscribed_and_new_buffers_after_yield(monkeypatch):
    followers, buffers, identity = _fanout_fixture()
    original_sleep = asyncio.sleep
    newcomer = LiveBuffer(4, 4096)
    yielded = False
    unsubscribed: LiveBuffer | None = None

    async def mutate_subscriptions_at_yield(delay=0):
        nonlocal yielded, unsubscribed
        assert delay == 0
        yielded = True
        unsubscribed = next(buffer for buffer in buffers if not buffer.values)
        followers._unsubscribe("owner", "s", unsubscribed)
        followers.live[("owner", "s")].add(newcomer)
        await original_sleep(0)

    monkeypatch.setattr(feed_module.asyncio, "sleep", mutate_subscriptions_at_yield)
    await followers.publish_live("owner", identity, 0, "partial")

    assert yielded
    assert unsubscribed is not None
    assert not unsubscribed.values
    assert sum(bool(buffer.values) for buffer in buffers) == 39
    assert not newcomer.values


async def test_cancelled_live_fanout_can_be_terminally_finished(monkeypatch):
    followers, buffers, identity = _fanout_fixture()
    original_sleep = asyncio.sleep
    yielded = asyncio.Event()

    async def cancel_at_yield(delay=0):
        assert delay == 0
        yielded.set()
        current = asyncio.current_task()
        assert current is not None
        current.cancel()
        await original_sleep(0)

    monkeypatch.setattr(feed_module.asyncio, "sleep", cancel_at_yield)
    task = asyncio.create_task(followers.publish_live("owner", identity, 0, "partial"))
    with pytest.raises(asyncio.CancelledError):
        await task
    assert yielded.is_set()
    assert followers.active

    followers.finish_live("owner", identity, "cancelled")
    assert not followers.active
    assert all(any(isinstance(item, ReplyReset) for item in buffer.values) for buffer in buffers)


async def test_old_phase_authorization_cannot_publish_after_scope_seal():
    auth_started, release_auth = asyncio.Event(), asyncio.Event()

    async def authorize(*args, **kwargs):
        auth_started.set()
        await release_auth.wait()

    gov = cast(
        Governance,
        SimpleNamespace(
            log=SimpleNamespace(session=SimpleNamespace(id="s", owner="owner")),
            authorize=authorize,
        ),
    )
    buffer = LiveBuffer(4, 4096)
    followers = Followers(cast(SessionAccess, SimpleNamespace()))
    followers.live[("owner", "s")] = {buffer}
    run = object.__new__(Run)
    old_scope = _LiveDisclosureScope()
    run._live_scope = old_scope
    run.gov = gov
    run.live_text = followers.publish_live

    async def finish_live(owner, identity, reason):
        followers.finish_live(owner, identity, reason)

    run.live_done = finish_live
    old_identity = AttemptIdentity(
        session_id="s",
        turn_id="old",
        model_call_id="call-old",
        attempt_id="old",
        purpose="answer",
    )

    async def old_phase_publish(owner, identity, offset, text):
        await run._live_delta(old_scope, owner, identity, offset, text)

    recorder = JournalAttemptRecorder(
        gov,
        RequestTags(
            session_id="s",
            turn_id="old",
            model_call_id="call-old",
            principal="owner",
            spec_id="demo",
            spec_version="1",
        ),
        "demo",
        live_text=old_phase_publish,
    )
    recorder.started[old_identity.attempt_id] = (old_identity, {}, "codec")
    recorder.live_offsets[old_identity.attempt_id] = 0
    late = asyncio.create_task(recorder.disclose_delta(old_identity, "late"))
    await auth_started.wait()

    await run._finish_live_scope(old_scope, None)
    new_scope = _LiveDisclosureScope()
    run._live_scope = new_scope
    release_auth.set()
    await late
    assert not buffer.values
    assert not followers.active

    new_identity = old_identity.model_copy(
        update={"turn_id": "new", "model_call_id": "call-new", "attempt_id": "new"}
    )
    await run._live_delta(new_scope, "owner", new_identity, 0, "current")
    await run._finish_live_scope(new_scope, None)
    assert await buffer.get() == ReplyText(
        session_id="s", turn_id="new", attempt_id="new", offset=0, text="current"
    )
    assert not buffer.blocked
    assert not buffer.closed_attempts


async def test_unexpected_finish_failure_resets_and_retires_live_attempt():
    buffer = LiveBuffer(4, 4096)
    followers = Followers(cast(SessionAccess, SimpleNamespace()))
    followers.live[("owner", "s")] = {buffer}
    run = object.__new__(Run)
    run._live_scope = None
    run.gov = cast(
        Governance, SimpleNamespace(log=SimpleNamespace(session=SimpleNamespace(owner="owner")))
    )
    run.live_text = followers.publish_live

    async def finish_live(owner, identity, reason):
        followers.finish_live(owner, identity, reason)
        if identity.attempt_id == "a":
            raise RuntimeError("cleanup failure")

    run.live_done = finish_live
    identity = AttemptIdentity(
        session_id="s", turn_id="t", model_call_id="c", attempt_id="a", purpose="answer"
    )
    second_identity = identity.model_copy(update={"attempt_id": "b", "model_call_id": "c2"})

    async def fail_after_delta(self, continuing=False, *, recovered_final=False):
        await self._live_delta(self._live_scope, "owner", identity, 0, "partial")
        await self._live_delta(self._live_scope, "owner", second_identity, 0, "second")
        raise RuntimeError("provider failure")

    run._finish_phase = MethodType(fail_after_delta, run)
    with pytest.raises(RuntimeError, match="provider failure") as failure:
        await run.finish()
    assert failure.value.__notes__ == ["live_provisional_cleanup_failed"]

    resets = (await buffer.get(), await buffer.get())
    assert resets == (
        ReplyReset(session_id="s", turn_id="t", attempt_id="a", reason="outage"),
        ReplyReset(session_id="s", turn_id="t", attempt_id="b", reason="outage"),
    )
    assert not buffer.blocked
    assert not buffer.closed_attempts
    assert not followers.active


async def test_cancelled_finish_seals_scope_and_resets_attempt():
    buffer = LiveBuffer(4, 4096)
    followers = Followers(cast(SessionAccess, SimpleNamespace()))
    followers.live[("owner", "s")] = {buffer}
    run = object.__new__(Run)
    run._live_scope = None
    run.gov = cast(
        Governance, SimpleNamespace(log=SimpleNamespace(session=SimpleNamespace(owner="owner")))
    )
    run.live_text = followers.publish_live

    async def finish_live(owner, identity, reason):
        followers.finish_live(owner, identity, reason)

    run.live_done = finish_live
    identity = AttemptIdentity(
        session_id="s", turn_id="t", model_call_id="c", attempt_id="a", purpose="answer"
    )
    published, hold = asyncio.Event(), asyncio.Event()

    async def wait_after_delta(self, continuing=False, *, recovered_final=False):
        await self._live_delta(self._live_scope, "owner", identity, 0, "partial")
        published.set()
        await hold.wait()

    run._finish_phase = MethodType(wait_after_delta, run)
    task = asyncio.create_task(run.finish())
    await published.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await buffer.get() == ReplyReset(
        session_id="s", turn_id="t", attempt_id="a", reason="cancelled"
    )
    assert not buffer.blocked
    assert not buffer.closed_attempts
    assert not followers.active


async def test_terminal_scope_seals_without_a_live_done_hook():
    run = object.__new__(Run)
    run.live_done = None
    scope = _LiveDisclosureScope()
    identity = AttemptIdentity(
        session_id="s", turn_id="t", model_call_id="c", attempt_id="a", purpose="answer"
    )
    scope.attempts[identity.attempt_id] = identity

    await run._finish_live_scope(scope, None)

    assert scope.sealed
    assert not scope.attempts
