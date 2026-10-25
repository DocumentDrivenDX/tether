"""Real native calls and durable reopen retain incomplete text without tool authority."""

from datetime import UTC, datetime, timedelta

import httpx2 as httpx
import pytest
from test_agent_context_restart import engine, spec
from test_compatible_adapter import CredentialProbe, adapter, wire_response
from test_gated_execution import DemoTool, final

from tether.engine.recovery import plan_resume
from tether.history.jsonl import JsonlHistory
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ScriptedModel
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.types.execution import Actor, CommitUnknown, NotInterrupted
from tether.types.storage import SessionInit, StoreError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize(
    ("wire_reason", "reason"),
    [
        ("length", "length"),
        ("content_filter", "filtered"),
        (None, "unknown"),
        ("future", "unknown"),
        ("", "unknown"),
    ],
)
@pytest.mark.parametrize("calls", [False, True])
async def test_native_incomplete_survives_actual_root_reopen(
    tmp_path, limits, wire_reason, reason, calls
):
    actor, tool = Actor(id="fabricated-owner", credential=CredentialProbe()), DemoTool()
    root = await JsonlRoot.open(tmp_path)
    store = await JsonlSessionStore.open(root)
    provider = await JsonlHistory.open(root)
    session = await store.create(actor.id, SessionInit())
    first = engine(limits, [final()], tool, provider)
    native = adapter(
        limits,
        lambda request: httpx.Response(
            200,
            json=wire_response(
                finish=wire_reason,
                calls=[
                    {
                        "id": "fabricated-call",
                        "type": "function",
                        "function": {"name": "write", "arguments": '{"count":3}'},
                    }
                ]
                if calls
                else None,
                usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
            ),
        ),
    )
    first.model = native
    try:
        async with store.turn(session.id, actor.id, turn_id="initial") as log:
            result = await first.run_turn(actor, log, "Fabricated", spec=spec())
        assert (result.stop_reason, result.reason) == ("model_incomplete", reason)
        assert result.messages[-1].content == "Fabricated answer."
    finally:
        assert await first.aclose()
        await native.aclose()
        assert await root.aclose()
    root = await JsonlRoot.open(tmp_path)
    provider = await JsonlHistory.open(root)
    second = engine(limits, [final()], tool, provider)
    try:
        store = await JsonlSessionStore.open(root)
        entries = (await store.entries(session.id, actor.id, start=0, limit=1000)).items
        original = await accepted_message(entries, provider, actor, session.id, reason)
        await native_observation(entries, provider, actor, wire_reason)
        assert original.content == "Fabricated answer."
        assert not original.tool_calls
        with pytest.raises(NotInterrupted):
            plan_resume(entries, {}, session_id=session.id)
        await measured_usage(store, session.id, actor)
        async with store.turn(session.id, actor.id, turn_id="next") as log:
            assert (
                await second.run_turn(actor, log, "Continue", spec=spec())
            ).stop_reason == "final"
        assert isinstance(second.model, ScriptedModel)
        assert any(m.content == original.content for m in second.model.requests[-1].messages)
        assert not tool.calls
    finally:
        assert await second.aclose()
        assert await root.aclose()


@pytest.mark.parametrize("phase", ["model_call", "turn_ended"])
async def test_unknown_acceptance_reopens_as_incomplete_not_executable(tmp_path, limits, phase):
    from tether.types import ModelResponse

    actor, tool, now = Actor(id="fabricated-owner"), DemoTool(), [datetime.now(UTC)]
    root = await JsonlRoot.open(tmp_path)
    store = await JsonlSessionStore.open(root, clock=lambda: now[0])
    provider = await JsonlHistory.open(root)
    session = await store.create(actor.id, SessionInit())
    response = ModelResponse(content="Partial", finish_reason="length")
    first = engine(limits, [response], tool, provider)
    inject_unknown(root, phase)
    try:
        with pytest.raises(CommitUnknown):
            async with store.turn(session.id, actor.id, turn_id="initial") as log:
                await first.run_turn(actor, log, "Fabricated", spec=spec())
        with pytest.raises(StoreError, match="requires_reopen"):
            await store.snapshot(session.id, actor.id)
        assert not tool.calls
    finally:
        assert await first.aclose()
        assert await root.aclose()
    now[0] += timedelta(minutes=1)
    root = await JsonlRoot.open(tmp_path)
    try:
        store = await JsonlSessionStore.open(root, clock=lambda: now[0])
        provider = await JsonlHistory.open(root)
        await store.recover(now=now[0])
        entries = (await store.entries(session.id, actor.id, start=0, limit=1000)).items
        assert not any(e.call_manifest for e in entries)
        message = await accepted_message(entries, provider, actor, session.id, "length")
        assert message.content == "Partial"
        assert not message.tool_calls
        if phase == "turn_ended":
            with pytest.raises(NotInterrupted):
                plan_resume(entries, {}, session_id=session.id)
        else:
            plan = plan_resume(entries, {}, session_id=session.id)
            assert [step.kind for step in plan.steps] == ["model_step"]
    finally:
        assert await root.aclose()


async def accepted_message(entries, provider, actor, sid, reason):
    call = next(e for e in entries if e.kind == "model_call")
    assert call.facts["status"] == "incomplete"
    assert call.facts["finish_reason"] == reason
    call_id = call.facts["model_call_id"]
    assert isinstance(call_id, str)
    source = next(e for e in entries if e.key == "source:" + call_id)
    assert not source.call_manifest
    assert source.facts["model_status"] == "incomplete"
    assert source.history_ref is not None
    return await MessageHistoryAdapter(provider).read(
        source.history_ref, actor=actor, session_id=sid
    )


async def measured_usage(store, sid, actor):
    summary = (await store.snapshot(sid, actor.id)).view.summary
    assert summary.usage.input_tokens == 7
    assert summary.usage.output_tokens == 3
    assert summary.usage_complete


def inject_unknown(root, phase):
    append = root._append

    def unknown(name, value, guard):
        offset = append(name, value, guard)
        if name == "journal" and any(e.get("kind") == phase for e in value.get("entries", [])):
            raise CommitUnknown("fabricated_incomplete_unknown")
        return offset

    root._append = unknown


async def native_observation(entries, provider, actor, wire_reason):
    from tether.types.execution import HistoryRef

    assert sum(e.kind == "model_attempt" for e in entries) == 1
    call = next(e for e in entries if e.kind == "model_call")
    ref = HistoryRef.model_validate(call.facts["exchange_ref"])
    values = await provider.read([ref], actor=actor)
    assert values[0].status == "ready"
    assert values[0].record is not None
    response = values[0].record.response
    assert isinstance(response, dict)
    assert response["status"] == 200
    body = response["body"]
    assert isinstance(body, dict)
    choices = body["choices"]
    assert isinstance(choices, list)
    choice = choices[0]
    assert isinstance(choice, dict)
    assert choice["finish_reason"] == wire_reason
    receipt = next(e for e in reversed(entries) if e.kind == "model_receipt").facts["receipt"]
    assert isinstance(receipt, dict)
    assert receipt["response_id"] == "fabricated-body-id"
