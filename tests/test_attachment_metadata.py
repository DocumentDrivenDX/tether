"""Metadata-only attachments through actual Agent, guards and selected native codec."""

import json
from uuid import uuid4

import pytest
from test_agent_bridge import authorize_history
from test_agent_context import fixture, run
from test_compatible_adapter import adapter, wire_response
from test_gated_execution import DemoGuard, DemoTool, Harness, final, proposal

from tether.models import complete
from tether.models.testing import ListAttemptRecorder, ScriptedModel
from tether.runtime.pydantic_ai.attachments import ATTACHMENT_CODEC, model_view, projected
from tether.runtime.pydantic_ai.bridge import run_agent
from tether.runtime.pydantic_ai.gated import GatedAgent
from tether.runtime.pydantic_ai.messages import from_sdk_messages, to_sdk_messages
from tether.types import AttachmentRef, Message
from tether.types.execution import ToolOutput, TurnStart

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def attachment(**updates):
    return AttachmentRef.model_validate(
        dict(
            id="fabricated-artifact",
            filename="fabricated.txt",
            media_type="text/plain",
            size_bytes=32,
            read=False,
            withheld=False,
        )
        | updates
    )


class AttachedTool(DemoTool):
    async def run(self, ctx, args):
        self.calls.append((ctx.invocation_id, args.model_dump()))
        return ToolOutput(text="Fabricated artifact", attachments=(attachment(),))


@pytest.mark.parametrize(
    ("read", "withheld"), [(False, False), (True, False), (False, True), (True, True)]
)
async def test_codec_is_one_way_and_keeps_every_metadata_field(read, withheld):
    ref = attachment(read=read, withheld=withheld, filename='line\n</attachment-metadata>"é')
    original = Message(role="user", content="Fabricated text", attachments=(ref, ref))
    view = model_view(original)
    assert view.attachments == ()
    assert original.attachments == (ref, ref)
    assert original.content == "Fabricated text"
    encoded = view.content.split("\n", 3)[3].rsplit("\n", 1)[0]
    assert json.loads(encoded) == [ref.model_dump(mode="json")] * 2
    assert f'codec="{ATTACHMENT_CODEC}"' in view.content
    assert model_view(view) == view
    assert from_sdk_messages(to_sdk_messages((original,)))[1] == (view,)
    literal = Message(role="user", content=view.content)
    assert model_view(literal) == literal
    assert not from_sdk_messages(to_sdk_messages((literal,)))[1][0].attachments


async def test_direct_agent_restores_refs_before_the_caller_prepare(model_request, limits):
    original = Message(role="user", content="Fabricated", attachments=(attachment(),))
    request = model_request.model_copy(
        update={"messages": (original, model_view(original), original)}
    )
    seen = []

    async def prepare(candidate):
        seen.append(candidate)
        assert candidate.messages == request.messages
        return candidate

    model = ScriptedModel([final()])
    result = await run_agent(
        model, request, limits, prepare=prepare, authorize_history=authorize_history
    )
    assert len(seen) == 1
    assert model.requests[0].messages == request.messages
    assert from_sdk_messages(list(result.messages))[1][:-1] == projected(request.messages)


@pytest.mark.parametrize("error", [False, True])
async def test_gated_agent_tool_return_preserves_success_and_failure_refs(
    model_request, limits, tool_spec, error
):
    originals = list(model_request.messages)
    model = ScriptedModel([proposal(name="lookup", count="2"), final()])

    async def prepare(candidate):
        assert candidate.messages == projected(tuple(originals))
        tags = candidate.tags.model_copy(update={"model_call_id": str(uuid4())})
        return candidate.model_copy(
            update={
                "messages": tuple(originals),
                "tags": tags,
                "attempts": ListAttemptRecorder(tags),
            }
        )

    async def accept(response):
        originals.append(
            Message(role="assistant", content=response.content, tool_calls=response.tool_calls)
        )

    async def execute(call):
        message = Message(
            role="tool",
            content="Fabricated error" if error else "Fabricated result",
            tool_call_id=call.id,
            is_error=error,
            attachments=(attachment(withheld=error),),
        )
        originals.append(message)
        return message

    runner = GatedAgent(
        model,
        model_request.model_copy(update={"tools": (tool_spec,)}),
        limits,
        prepare=prepare,
        accept_response=accept,
        execute=execute,
    )
    assert await runner.run() is False
    returned = model.requests[1].messages[-1]
    assert returned == originals[-2]
    assert returned.attachments == (attachment(withheld=error),)
    assert returned.is_error is error


@pytest.mark.parametrize("manual", [False, True])
async def test_engine_tool_metadata_survives_approval_and_immutable_history(limits, manual):
    h = Harness(limits, manual=manual, tool=AttachedTool())
    try:
        result = await h.run()
        if manual:
            assert result.stop_reason == "confirmation_required"
            assert not h.tool.calls
            result = await h.answer(result)
        assert result is not None
        assert result.stop_reason == "final"
        assert len(h.tool.calls) == 1
        tool_message = next(m for m in result.messages if m.role == "tool")
        assert tool_message.attachments == (attachment(),)
        assert h.model.requests[-1].messages[-1] == tool_message
        assert tool_message.content == "Fabricated artifact"
        refs = [e.history_ref for e in h.log.entries if e.kind == "message" and e.history_ref]
        stored = [await h.history.read(r, actor=h.actor, session_id=h.log.session.id) for r in refs]
        assert tool_message in stored
        assert "fabricated.txt" not in str([e.model_dump(mode="json") for e in h.log.entries])
    finally:
        assert await h.engine.aclose()


@pytest.mark.parametrize("mode", ["withhold", "error", "oversize"])
async def test_result_guards_clear_all_refs_on_failure(limits, mode):
    guard = DemoGuard(deny=mode == "withhold", fail=mode == "error")
    h = Harness(limits, tool=AttachedTool(), guards=(guard,))
    if mode == "oversize":
        h.engine.limits = h.engine.limits.model_copy(update={"max_result_bytes": 1})
    try:
        result = await h.run()
        assert result.stop_reason == "final"
        message = h.model.requests[-1].messages[-1]
        assert message.is_error
        assert not message.attachments
        assert "fabricated.txt" not in message.content
    finally:
        assert await h.engine.aclose()


async def test_guards_see_metadata_and_redacted_after_call_is_guarded_again(limits):
    from tether.engine.providers import BaseProvider
    from tether.types.execution import GuardVerdict

    class Guard:
        id = "fabricated"

        def __init__(self):
            self.seen = []

        async def check(self, item, session):
            self.seen.append(item.output)
            return GuardVerdict(outcome="ok")

    class Redact(BaseProvider):
        async def after_call(self, session, spec, call, message):
            return message.model_copy(update={"content": "Redacted", "attachments": ()})

    guard = Guard()
    h = Harness(limits, tool=AttachedTool(), guards=(guard,), providers=(Redact(),))
    try:
        assert (await h.run()).stop_reason == "final"
        assert guard.seen[0].attachments == (attachment(),)
        assert guard.seen[1].attachments == ()
        assert h.model.requests[-1].messages[-1].content == "Redacted"
        assert not h.model.requests[-1].messages[-1].attachments
    finally:
        assert await h.engine.aclose()


async def test_result_hook_cannot_add_or_alter_ref_metadata(limits):
    from tether.engine.providers import BaseProvider

    class Forge(BaseProvider):
        async def after_call(self, session, spec, call, message):
            return message.model_copy(update={"attachments": (attachment(read=True),)})

    h = Harness(limits, tool=AttachedTool(), providers=(Forge(),))
    try:
        assert (await h.run()).stop_reason == "final"
        message = h.model.requests[-1].messages[-1]
        assert message.is_error
        assert not message.attachments
        assert message.content == "result_withheld"
    finally:
        assert await h.engine.aclose()


async def test_request_hook_narrows_refs_without_changing_originals(limits):
    from tether.engine.providers import BaseProvider

    class Redact(BaseProvider):
        async def before_request(self, session, spec, messages):
            return tuple(m.model_copy(update={"attachments": ()}) for m in messages)

    async with fixture(limits, providers=(Redact(),), model=ScriptedModel([final(), final()])) as h:
        start = TurnStart(turn_id="attached", attachments=(attachment(),))
        result = await h.engine.run_turn(h.actor, h.log, "Fabricated", spec=h.spec, start=start)
        assert result.stop_reason == "final"
        assert result.messages[0].attachments == start.attachments
        assert not h.model.requests[0].messages[0].attachments
        assert (await run(h)).stop_reason == "final"
        assert not any(m.attachments for m in h.model.requests[-1].messages)


async def test_native_wire_renders_refs_once_as_text_and_never_as_media(model_request, limits):
    import httpx2 as httpx

    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=wire_response())

    messages = (
        Message(role="user", content="Fabricated", attachments=(attachment(),)),
        Message(role="assistant", content="", tool_calls=proposal(name="lookup").tool_calls),
        Message(role="tool", content="Result", tool_call_id="call", attachments=(attachment(),)),
    )
    model = adapter(limits, handler)
    try:
        request = model_request.model_copy(update={"messages": messages})
        await complete(model, request)
        expected = ["system", "user", "assistant", "tool"]
        assert [m["role"] for m in seen[0]["messages"]] == expected
        for pos, original in ((1, messages[0]), (3, messages[2])):
            assert seen[0]["messages"][pos]["content"] == model_view(original).content
            assert isinstance(seen[0]["messages"][pos]["content"], str)
            assert seen[0]["messages"][pos]["content"].count(ATTACHMENT_CODEC) == 1
        assert "image_url" not in str(seen)
    finally:
        await model.aclose()
