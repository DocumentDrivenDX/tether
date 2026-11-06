"""The public host forwards optional instrumentation and clocks to its engine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import count

import pytest
from public_port_fixtures import FabricatedTool, RecordingModel

from tether.client import (
    Actor,
    AgentSpec,
    AttemptIdentity,
    ClientHost,
    ClientPorts,
    ClientSettings,
    Decision,
    ExecutionLimits,
    ModelResponse,
    ResponseReceipt,
    ToolCall,
    VolatileSettings,
)
from tether.client.testing import fabricated_model_context, scripted_model
from tether.testing.telemetry import RecordingTracer
from tether.types.telemetry import TraceIds

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _AttemptRecorder:
    def __init__(self, tags):
        self.tags = tags
        self.identities: list[AttemptIdentity] = []
        self.starts = []
        self.observations = []

    async def reserve(self):
        identity = AttemptIdentity(
            session_id=self.tags.session_id,
            turn_id=self.tags.turn_id,
            model_call_id=self.tags.model_call_id,
            attempt_id="public-client-port-attempt",
            purpose=self.tags.purpose,
        )
        self.identities.append(identity)
        return identity

    async def start(self, identity, *, request_digest, wire_request, codec="model-port-v1"):
        self.starts.append((identity, request_digest, wire_request, codec))

    async def observe(self, identity, receipt, *, wire_response=None, usage=None):
        self.observations.append((identity, receipt, wire_response, usage))


def _trace_id_factory():
    serial = count(1)

    def create(parent):
        value = next(serial)
        return TraceIds(
            trace_id=parent.trace_id if parent is not None else f"{value:032x}",
            span_id=f"{value:016x}",
            trace_flags=1,
        )

    return create


async def test_from_ports_forwards_attempt_factory_tracer_and_clock(client_limits, limits):
    """@covers US-065-AC2: prove the public host forwards three optional ports."""
    fixed = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    tool = FabricatedTool()
    receipt = ResponseReceipt(
        provider="fabricated",
        endpoint="offline-host-port-test",
        schema_profile="fabricated-v1",
        request_id_source="none",
    )
    model = RecordingModel(
        scripted_model(
            [
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            id="host-port-call",
                            name=tool.name,
                            arguments={"label": "fake"},
                        ),
                    ),
                    receipt=receipt,
                    finish_reason="tool_calls",
                )
            ]
        )
    )
    context = fabricated_model_context()
    policy = _Allow()
    settings = ClientSettings(
        model=model,
        storage=VolatileSettings(),
        policy=policy,
        client_limits=client_limits,
        model_limits=limits,
        context_config=context["context_config"],
        estimator=context["estimator"],
        specs=(
            AgentSpec(
                id="demo",
                version="1",
                title="Fabricated",
                instructions="Fabricated",
                model="demo",
                tools=(tool.name,),
            ),
        ),
        tools=(tool,),
        resume="off",
        default_spec="demo",
    )
    created = []
    recorders = []

    def attempt_factory(tags):
        created.append(tags)
        recorder = _AttemptRecorder(tags)
        recorders.append(recorder)
        return recorder

    tracer = RecordingTracer(_trace_id_factory(), capacity=64)
    clock_calls = []

    def fixed_clock():
        clock_calls.append(True)
        return fixed

    ports = ClientPorts(
        attempt_factory=attempt_factory,
        tracer=tracer,
        clock=fixed_clock,
        execution_limits=ExecutionLimits(approval_ttl_seconds=17),
    )
    async with await ClientHost.from_ports(settings, ports=ports) as host:
        client = host.client(Actor(id="fabricated-owner"))
        session = await client.create(ref="host-port-wiring", spec="demo")
        turn = await client.send(session.id, "Fabricated request")
        result = await turn.result()
        assert result.stop_reason == "confirmation_required"
        pending = result.pending[0]
        assert pending.expires_at == fixed + timedelta(seconds=17)
        assert clock_calls

    assert len(created) == 1
    assert created[0].session_id == session.id
    assert recorders[0].identities[0].attempt_id == "public-client-port-attempt"
    assert len(recorders[0].starts) == 1
    assert recorders[0].observations[0][1] == receipt
    assert policy.requests
    spans = [span for span in tracer.records if span.name == "tether.model_call"]
    assert spans
    assert all(span.finished for span in spans)


class _Allow:
    def __init__(self):
        self.requests = []

    async def authorize(self, request):
        self.requests.append(request)
        return Decision(allowed=True, reason="fabricated_allow")
