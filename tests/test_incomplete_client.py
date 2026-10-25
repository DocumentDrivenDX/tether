"""Client outcome reconstruction and metadata observers preserve incomplete status."""

import pytest
from test_client_reads import CurrentPolicy
from test_engine_telemetry import id_factory, make_engine, spec

from tether import Tether
from tether.context.testing import fabricated_context
from tether.engine import Engine
from tether.history.jsonl import JsonlHistory
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ScriptedModel
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.testing.execution import MemoryTurnLog
from tether.testing.telemetry import ListAuditSink, RecordingTracer
from tether.types import ModelResponse, TokenUsage
from tether.types.execution import Actor, ExecutionDenied, SessionState
from tether.types.storage import SessionInit

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("reason", ["length", "filtered", "unknown"])
async def test_client_live_and_reopened_result_keep_incomplete_text_and_current_reads(
    tmp_path, limits, client_limits, reason
):
    actor, policy = Actor(id="owner"), CurrentPolicy()
    expected = None
    sid = None
    turn_id = None
    for reopen in (False, True):
        root = await JsonlRoot.open(tmp_path)
        store, history = await JsonlSessionStore.open(root), await JsonlHistory.open(root)
        model = ScriptedModel(
            [] if reopen else [ModelResponse(content="Partial", finish_reason=reason)]
        )
        engine = Engine(
            **fabricated_context(),
            model=model,
            history=MessageHistoryAdapter(history),
            history_provider=history,
            model_limits=limits,
            specs=(spec(),),
            policy=policy,
        )
        async with Tether(engine=engine, store=store, history=history, limits=client_limits) as app:
            if sid is None:
                sid = (await store.create(actor.id, SessionInit(spec="fixture"))).id
            handle = await app.client(actor).send(sid, "Fabricated", request_id="same")
            result = await handle.result()
            assert (result.stop_reason, result.reason) == ("model_incomplete", reason)
            assert result.messages[-1].content == "Partial"
            assert not result.pending
            assert not result.messages[-1].tool_calls
            if reopen:
                assert result == expected
                assert handle.turn_id == turn_id
                assert not model.requests
                assert not app.turns.jobs
            else:
                expected, turn_id = result, handle.turn_id
                assert len(model.requests) == 1
            policy.read = False
            with pytest.raises(ExecutionDenied):
                await handle.result()
            policy.read = True
            assert await handle.result() == result
        assert app.drained


@pytest.mark.parametrize("reason", ["length", "filtered", "unknown"])
async def test_incomplete_audit_is_exact_metadata_without_content(limits, reason):
    sink, tracer = ListAuditSink(capacity=128), RecordingTracer(id_factory(), capacity=128)
    engine = make_engine(
        limits,
        audit=sink,
        tracer=tracer,
        model=ScriptedModel(
            [
                ModelResponse(
                    content="Fabricated private reply marker",
                    finish_reason=reason,
                    usage=TokenUsage(input_tokens=7, output_tokens=3),
                )
            ]
        ),
    )
    log = MemoryTurnLog(SessionState(id="session", owner="owner"), turn_id="phase")
    try:
        assert (
            await engine.run_turn(Actor(id="owner"), log, "Fabricated", spec=spec())
        ).stop_reason == "model_incomplete"
    finally:
        assert await engine.aclose()
    call = next(r for r in sink.records if r.kind == "model_call")
    ended = next(r for r in sink.records if r.kind == "turn_ended")
    assert call.detail["status"] == "incomplete"
    assert call.detail["finish_reason"] == reason
    assert ended.detail["stop_reason"] == "model_incomplete"
    assert ended.detail["reason"] == reason
    assert "Fabricated private reply marker" not in str([r.model_dump() for r in sink.records])
    span = next(r for r in tracer.records if r.name == "tether.model_call")
    assert span.attributes["tether.input_tokens"] == 7
    assert span.attributes["tether.output_tokens"] == 3
