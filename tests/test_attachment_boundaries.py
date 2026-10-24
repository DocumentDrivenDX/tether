"""Original refs remain authoritative across SDK tampering, compaction and root reopen."""

import json

import pytest
from test_agent_context import fixture
from test_agent_context_restart import engine, spec
from test_agent_summaries import fixture as summary_fixture
from test_agent_summaries import response, turn
from test_attachment_metadata import AttachedTool, attachment
from test_gated_execution import final, proposal

from tether.history.jsonl import JsonlHistory
from tether.models.testing import ScriptedModel
from tether.runtime.pydantic_ai import bridge
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.types.execution import Actor, Answer, TurnStart
from tether.types.storage import SessionInit

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("change", ["text", "drop", "duplicate"])
async def test_sdk_projection_changes_cannot_restore_or_disclose_original_refs(
    limits, monkeypatch, change
):
    original = bridge.from_sdk_messages

    def tamper(messages):
        system, converted = original(messages)
        if change == "text":
            converted = (converted[0].model_copy(update={"content": "Forged"}),)
        elif change == "drop":
            converted = ()
        else:
            converted = (*converted, converted[0])
        return system, converted

    monkeypatch.setattr(bridge, "from_sdk_messages", tamper)
    async with fixture(limits) as h:
        result = await h.engine.run_turn(
            h.actor,
            h.log,
            "Fabricated",
            spec=h.spec,
            start=TurnStart(turn_id="attached", attachments=(attachment(),)),
        )
        assert (result.stop_reason, result.reason) == ("model_denied", "derived_history_changed")
        assert not h.model.requests
        assert not any(e.kind == "context_use" for e in h.log.entries)


async def test_summary_payload_contains_only_bounded_ref_metadata(limits):
    tool = AttachedTool()
    async with summary_fixture(
        limits, tools=(tool,), script=[proposal(), final(), response("Metadata summary"), final()]
    ) as h:
        h.spec = h.spec.model_copy(update={"tools": (tool.name,)})
        assert (await turn(h, "first"))[0].stop_reason == "final"
        result, _ = await turn(h, "second")
        assert result.stop_reason == "final"
        auxiliary = next(r for r in h.model.requests if r.tags.purpose == "summary")
        payload = json.loads(auxiliary.messages[0].content.split("\n", 1)[1].rsplit("\n", 1)[0])
        refs = [a for s in payload["originals"] for a in s["message"]["attachments"]]
        assert refs == [attachment().model_dump(mode="json")]
        assert "no file contents were loaded" in auxiliary.system
        assert not auxiliary.tools
        assert "Metadata summary" in str(h.model.requests[-1].messages)


async def test_actual_root_reopen_restores_user_and_approved_tool_refs(tmp_path, limits):
    actor, tool = Actor(id="fabricated-owner"), AttachedTool()
    root = await JsonlRoot.open(tmp_path)
    store = (await JsonlSessionStore.open(root)).for_actor(actor)
    provider = await JsonlHistory.open(root)
    session = await store.create(actor.id, SessionInit(settings={"approval": "manual"}))
    first = engine(limits, [proposal()], tool, provider)
    try:
        async with store.turn(session.id, actor.id, turn_id="first") as log:
            pending = await first.run_turn(
                actor,
                log,
                "Fabricated",
                spec=spec(),
                start=TurnStart(turn_id="first", attachments=(attachment(read=True),)),
            )
        assert pending.stop_reason == "confirmation_required"
        assert not tool.calls
    finally:
        assert await first.aclose()
        assert await root.aclose()
    await verify_reopened(tmp_path, limits, actor, session, tool, pending.pending[0].invocation_id)


async def verify_reopened(path, limits, actor, session, tool, invocation):
    root = await JsonlRoot.open(path)
    store = (await JsonlSessionStore.open(root)).for_actor(actor)
    provider = await JsonlHistory.open(root)
    second = engine(limits, [final()], tool, provider)
    try:
        async with store.turn(session.id, actor.id, turn_id="answer") as log:
            result = await second.answer(actor, log, invocation, Answer(approved=True))
            assert result is not None
            assert result.stop_reason == "final"
            assert len(tool.calls) == 1
            assert isinstance(second.model, ScriptedModel)
            request = second.model.requests[0]
            assert request.messages[0].attachments == (attachment(read=True),)
            assert request.messages[-1].attachments == (attachment(),)
            assert request.messages[0].content == "Fabricated"
            for entry in log.entries:
                if entry.kind == "message" and entry.facts["role"] in {"user", "tool"}:
                    assert entry.history_ref is not None
                    message = await second.history.read(
                        entry.history_ref, actor=actor, session_id=session.id
                    )
                    assert message.attachments
    finally:
        assert await second.aclose()
        assert await root.aclose()


async def test_tool_retained_ref_cannot_change_metadata_after_guard_copy(limits):
    import asyncio

    from test_gated_execution import Harness

    from tether.types.execution import GuardVerdict, ToolOutput

    captured, release = asyncio.Event(), asyncio.Event()
    retained = attachment()

    class Tool(AttachedTool):
        async def run(self, ctx, args):
            return ToolOutput(text="Fabricated", attachments=(retained,))

    class Guard:
        id = "fabricated"

        async def check(self, item, session):
            assert item.output.attachments[0].filename == "fabricated.txt"
            captured.set()
            await release.wait()
            return GuardVerdict(outcome="ok")

    h = Harness(limits, tool=Tool(), guards=(Guard(),))
    task = asyncio.create_task(h.run())
    try:
        async with asyncio.timeout(2):
            await captured.wait()
        object.__setattr__(retained, "filename", "unchecked.txt")
        release.set()
        result = await task
        assert result.stop_reason == "final"
        message = h.model.requests[-1].messages[-1]
        assert message.attachments[0].filename == "fabricated.txt"
        assert "unchecked.txt" not in str(result.messages)
        source = next(e for e in h.log.entries if e.kind == "message" and e.facts["role"] == "tool")
        assert source.history_ref is not None
        stored = await h.history.read(
            source.history_ref, actor=h.actor, session_id=h.log.session.id
        )
        assert stored == message
    finally:
        release.set()
        await task
        assert await h.engine.aclose()


async def test_direct_agent_ref_restoration_refuses_forged_sdk_projection(
    model_request, limits, monkeypatch
):
    from test_agent_bridge import authorize_history, prepared

    from tether.runtime.pydantic_ai.bridge import run_agent
    from tether.types import Message, ModelAttemptDenied

    original = bridge.from_sdk_messages

    def tamper(messages):
        system, converted = original(messages)
        return system, (*converted, converted[0])

    monkeypatch.setattr(bridge, "from_sdk_messages", tamper)
    model = ScriptedModel([final()])
    request = model_request.model_copy(
        update={
            "messages": (Message(role="user", content="Fabricated", attachments=(attachment(),)),)
        }
    )
    with pytest.raises(ModelAttemptDenied, match="derived_history_changed"):
        await run_agent(
            model, request, limits, prepare=prepared, authorize_history=authorize_history
        )
    assert not model.requests
