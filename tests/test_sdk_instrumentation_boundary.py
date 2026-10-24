"""Ambient SDK instrumentation cannot opt Tether into content-bearing SDK tracing."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from opentelemetry import trace
from pydantic_ai import Agent
from pydantic_ai.models.instrumented import InstrumentationSettings
from pydantic_ai.models.test import TestModel

from tether.models.testing import ScriptedModel
from tether.runtime.pydantic_ai.bridge import run_agent
from tether.types import ModelResponse

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class CountingTracer(trace.NoOpTracer):
    def __init__(self):
        self.names = []

    def start_span(self, name, *args, **kwargs):
        self.names.append(name)
        return super().start_span(name, *args, **kwargs)

    def start_as_current_span(self, name, *args, **kwargs):
        self.names.append(name)
        return super().start_as_current_span(name, *args, **kwargs)


class CountingProvider(trace.TracerProvider):
    def __init__(self, tracer):
        self.tracer = tracer

    def get_tracer(self, *args, **kwargs):
        return self.tracer


@asynccontextmanager
async def ambient_instrumentation():
    tracer = CountingTracer()
    settings = InstrumentationSettings(tracer_provider=CountingProvider(tracer))
    Agent.instrument_all(settings)
    try:
        control = Agent(TestModel(custom_output_text="Fabricated control"))
        await control.run("Fabricated input")
        assert tracer.names
        tracer.names.clear()
        yield tracer
    finally:
        Agent.instrument_all(False)


async def test_ambient_sdk_instrumentation_is_ignored_by_tether_bridge(model_request, limits):
    async with ambient_instrumentation() as tracer:

        async def prepare(request):
            return request

        async def authorize():
            return None

        response = ModelResponse(content="Fabricated answer", finish_reason="stop")
        result = await run_agent(
            ScriptedModel([response]),
            model_request,
            limits,
            prepare=prepare,
            authorize_history=authorize,
        )
        assert result.response == response
        assert not tracer.names


async def test_ambient_sdk_instrumentation_is_ignored_by_gated_engine(limits):
    from tether.engine import Engine
    from tether.testing.execution import MemoryHistory, MemoryTurnLog
    from tether.types.execution import Actor, AgentSpec, SessionState

    async with ambient_instrumentation() as tracer:
        actor = Actor(id="fabricated")
        log = MemoryTurnLog(SessionState(id="fabricated", owner=actor.id))
        engine = Engine(
            model=ScriptedModel([ModelResponse(content="Fabricated", finish_reason="stop")]),
            history=MemoryHistory(),
            model_limits=limits,
        )
        spec = AgentSpec(
            id="demo", version="1", title="Demo", instructions="Fabricated", model="demo"
        )
        try:
            result = await engine.run_turn(actor, log, "Fabricated", spec=spec)
            assert result.stop_reason == "final"
            assert not tracer.names
        finally:
            assert await engine.aclose()
