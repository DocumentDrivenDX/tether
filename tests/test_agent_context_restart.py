"""A real root reopen reconciles accepted context identities; caches prove nothing."""

from datetime import UTC, datetime, timedelta

import pytest
from test_gated_execution import DemoTool, final, proposal

from tether.context.testing import fabricated_context
from tether.engine import Engine
from tether.history.jsonl import JsonlHistory
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ScriptedModel
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.types.execution import Actor, AgentSpec, Answer, CommitUnknown
from tether.types.storage import SessionInit, StoreError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def spec():
    return AgentSpec(
        id="demo",
        version="1",
        title="Demo",
        instructions="Fabricated",
        model="demo",
        tools=("write",),
    )


def engine(limits, script, tool, provider):
    return Engine(
        **fabricated_context(),
        model=ScriptedModel(script),
        history=MessageHistoryAdapter(provider),
        history_provider=provider,
        model_limits=limits,
        tools=(tool,),
        specs=(spec(),),
    )


@pytest.mark.parametrize("kind", ["context_preparation", "context_use"])
async def test_unknown_context_ack_requires_actual_root_reopen_and_fresh_ordinal(
    tmp_path, limits, kind
):
    actor, tool, now = Actor(id="fabricated-owner"), DemoTool(), [datetime.now(UTC)]
    root = await JsonlRoot.open(tmp_path)
    source = await JsonlSessionStore.open(root, clock=lambda: now[0])
    store = source.for_actor(actor)
    provider = await JsonlHistory.open(root)
    session = await store.create(actor.id, SessionInit())
    first = engine(limits, [final()], tool, provider)
    append = root._append

    def unknown(name, value, guard):
        offset = append(name, value, guard)
        if (
            name == "journal"
            and isinstance(value, dict)
            and any(e.get("kind") == kind for e in value.get("entries", []))
        ):
            raise CommitUnknown("fabricated_context_unknown")
        return offset

    root._append = unknown
    try:
        with pytest.raises(CommitUnknown, match="fabricated_context_unknown"):
            async with store.turn(session.id, actor.id, turn_id="first") as log:
                await first.run_turn(actor, log, "Fabricated first", spec=spec())
        assert isinstance(first.model, ScriptedModel)
        assert not first.model.requests
        with pytest.raises(StoreError, match="requires_reopen"):
            await store.snapshot(session.id, actor.id)
    finally:
        assert await first.aclose()
        assert await root.aclose()
    now[0] += timedelta(minutes=1)
    root = await JsonlRoot.open(tmp_path)
    try:
        await assert_reopened(root, actor, session, tool, limits, now, kind)
    finally:
        assert await root.aclose()


async def assert_reopened(root, actor, session, tool, limits, now, kind):
    store = (await JsonlSessionStore.open(root, clock=lambda: now[0])).for_actor(actor)
    provider = await JsonlHistory.open(root)
    second = engine(limits, [final()], tool, provider)
    try:
        async with store.turn(session.id, actor.id, turn_id="fresh") as log:
            result = await second.run_turn(actor, log, "Fabricated fresh request", spec=spec())
            assert result.stop_reason == "final"
            assert [e.facts["ordinal"] for e in log.entries if e.kind == "context_preparation"] == [
                0,
                1,
            ]
            uses = [e for e in log.entries if e.kind == "context_use"]
            assert [e.facts["preparation_ordinal"] for e in uses] == (
                [0, 1] if kind == "context_use" else [1]
            )
            assert isinstance(second.model, ScriptedModel)
            assert len(second.model.requests) == 1
            call = second.model.requests[0].tags.model_call_id
            grouped = await store.lookup(
                session.id, actor.id, kind="model_call", value=call, limit=100, max_bytes=65536
            )
            assert {
                "context_preparation",
                "context_use",
                "model_attempt",
                "model_receipt",
                "model_call",
            }.issubset({e.kind for e in grouped})
    finally:
        assert await second.aclose()


async def test_approval_after_reopen_keeps_context_identity_and_original_tool_group(
    tmp_path, limits
):
    actor, tool = Actor(id="fabricated-owner"), DemoTool()
    root = await JsonlRoot.open(tmp_path)
    store = (await JsonlSessionStore.open(root)).for_actor(actor)
    provider = await JsonlHistory.open(root)
    session = await store.create(actor.id, SessionInit(settings={"approval": "manual"}))
    first = engine(limits, [proposal()], tool, provider)
    async with store.turn(session.id, actor.id, turn_id="first") as log:
        waiting = await first.run_turn(actor, log, "Fabricated request", spec=spec())
        assert waiting.stop_reason == "confirmation_required"
    assert await first.aclose()
    assert await root.aclose()
    root = await JsonlRoot.open(tmp_path)
    try:
        store = (await JsonlSessionStore.open(root)).for_actor(actor)
        provider = await JsonlHistory.open(root)
        second = engine(limits, [final()], tool, provider)
        try:
            async with store.turn(session.id, actor.id, turn_id="answer") as log:
                result = await second.answer(
                    actor, log, waiting.pending[0].invocation_id, Answer(approved=True)
                )
                assert result is not None
                assert result.stop_reason == "final"
                assert len(tool.calls) == 1
                assert [
                    e.facts["ordinal"] for e in log.entries if e.kind == "context_preparation"
                ] == [0, 1]
                assert isinstance(second.model, ScriptedModel)
                assert [m.role for m in second.model.requests[0].messages] == [
                    "user",
                    "assistant",
                    "tool",
                ]
        finally:
            assert await second.aclose()
    finally:
        assert await root.aclose()
