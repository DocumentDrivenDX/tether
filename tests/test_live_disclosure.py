"""Provisional text requires a started physical identity and current READ_SESSION."""

from types import SimpleNamespace

import pytest

from tether.engine.attempts import JournalAttemptRecorder
from tether.types import ModelAttemptDenied, ModelResponse, TextDelta
from tether.types.canonical import digest
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

    async def disclose(identity, offset, text):
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

    async def disclose(identity, offset, text):
        events.append((identity, offset, text))

    def fail_admission():
        raise ContextError("context_invalid")

    gov = SimpleNamespace(
        actor=Actor(id=tags.principal),
        log=SimpleNamespace(session=SimpleNamespace(id=tags.session_id)),
        observers=SimpleNamespace(tracer=SimpleNamespace(current_ids=lambda: None)),
        authorize=authorize,
        fact=fact,
    )
    recorder = JournalAttemptRecorder(gov, tags, "demo", admit=fail_admission, live_text=disclose)
    identity = await recorder.reserve()
    wire = {"request": "bounded"}
    with pytest.raises(ContextError, match="context_invalid"):
        await recorder.start(identity, request_digest=digest(wire), wire_request=wire)
    with pytest.raises(ModelAttemptDenied, match="invalid_attempt_identity"):
        await recorder.disclose_delta(identity, "unaccepted")
    assert events == []


async def test_current_read_policy_is_rechecked_before_each_delta(harness):
    h = harness
    events = []

    async def disclose(identity, offset, text):
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

    async def disclose(identity, offset, text):
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
