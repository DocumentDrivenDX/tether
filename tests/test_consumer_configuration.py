"""Default composition keeps finite bounds and caller-selected telemetry names."""

import asyncio

import pytest
from test_gated_execution import DemoTool, final, proposal
from test_telemetry_ports import id_factory

from tether import Tether
from tether.context import FullContext, TailContext
from tether.context.testing import fabricated_context
from tether.engine import Engine
from tether.engine.observers.tracing import GuardedTracer
from tether.history.jsonl import JsonlHistory
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ScriptedModel
from tether.stores.memory import MemoryFiles, MemorySessionStore
from tether.testing.execution import MemoryHistory, MemoryTurnLog
from tether.testing.telemetry import RecordingTracer
from tether.types import ModelExecutionLimits
from tether.types.client import ClientLimits
from tether.types.context import ContextConfig, ContextError, ModelLimits
from tether.types.execution import Actor, AgentSpec, SessionState

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def spec():
    return AgentSpec(id="fixture", version="1", title="Fixture", instructions="", model="fixture")


async def test_minimal_client_composition_and_explicit_context_capacity():
    files = MemoryFiles()
    history = await JsonlHistory.open(files)
    engine = Engine(
        model=ScriptedModel([final()]),
        history=MessageHistoryAdapter(history),
        history_provider=history,
        specs=(spec(),),
        **fabricated_context(),
    )
    async with Tether(
        engine=engine,
        store=await MemorySessionStore.open(files),
        history=history,
        resume="off",
        default_spec="fixture",
    ) as app:
        client = app.client(Actor(id="owner"))
        session = await client.create()
        result = await (await client.send(session.id, "Fabricated request")).result()
        assert result.stop_reason == "final"
        assert (await client.history(session.id)).items
        assert app.limits == ClientLimits()
        assert engine.model_limits == ModelExecutionLimits()
        assert isinstance(engine.context.strategy, FullContext)
        with pytest.raises(ContextError, match="context_unconfigured"):
            engine.context.configuration("unknown")


async def test_model_profile_convenience_preserves_declared_capacity_and_overrides():
    model = ModelLimits(
        profile_id="fabricated",
        revision="1",
        context_window_tokens=1000,
        output_tokens=100,
        safety_margin_tokens=50,
    )
    config = ContextConfig(limits={"fixture": model})
    engine = Engine(
        model=ScriptedModel([final()]),
        history=MemoryHistory(),
        context_config=config,
        estimator=fabricated_context()["estimator"],
        context=TailContext(),
        model_limits=ModelExecutionLimits(max_attempts=1),
    )
    try:
        assert engine.context.configuration("fixture").limits["fixture"].input_tokens == 850
        assert engine.model_limits.max_attempts == 1
        assert isinstance(engine.context.strategy, TailContext)
        config.limits.clear()
        assert engine.context.configuration("fixture").limits
    finally:
        await engine.aclose()


@pytest.mark.parametrize("failed", [False, True])
async def test_all_nested_worker_spans_use_selected_names_without_content(failed):
    tracer = RecordingTracer(id_factory(), capacity=128)
    tool = DemoTool()
    if failed:
        tool.failure = RuntimeError("private result marker")
    engine = Engine(
        model=ScriptedModel([proposal(), final()]),
        history=MemoryHistory(),
        tools=(tool,),
        tracer=tracer,
        span_prefix="novirhiza.harness",
        attribute_namespace="fabricated",
        **fabricated_context(),
    )
    definition = spec().model_copy(update={"tools": (tool.name,)})
    log = MemoryTurnLog(SessionState(id="session", owner="owner"), turn_id="phase")
    try:
        await engine.run_turn(Actor(id="owner"), log, "private prompt marker", spec=definition)
        await asyncio.sleep(0)
        records = tracer.records
        assert records
        assert any(value.name.endswith(".async_body") for value in records)
        assert all(value.name.startswith("novirhiza.harness.") for value in records)
        assert all(key.startswith("fabricated.") for value in records for key in value.attributes)
        assert any("fabricated.usage_complete" in value.attributes for value in records)
        assert all(value.finished for value in records)
        assert "private prompt marker" not in str(records)
        assert "private result marker" not in str(records)
        assert all(not entry.kind.startswith("novirhiza") for entry in log.entries)
    finally:
        await engine.aclose()


@pytest.mark.parametrize("value", ["", "a b", ".invalid", "x" * 129, None, 3])
async def test_invalid_telemetry_namespace_refuses_before_execution(value):
    with pytest.raises(ValueError, match="telemetry_namespace_invalid"):
        GuardedTracer(None, span_prefix=value)
    with pytest.raises(ValueError, match="telemetry_namespace_invalid"):
        GuardedTracer(None, attribute_namespace=value)
