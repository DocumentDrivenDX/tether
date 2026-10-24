"""Operational metadata follows known commits and current execution identities."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from test_gated_execution import DemoTool, final, proposal
from test_telemetry_ports import id_factory

from tether import Tether
from tether.context.testing import fabricated_context
from tether.engine import Engine
from tether.engine.governance import Governance
from tether.engine.observers.facade import Observers
from tether.engine.observers.metadata import Scope, record
from tether.history.jsonl import JsonlHistory
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ListAttemptRecorder, ScriptedModel
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.testing.execution import MemoryHistory, MemoryTurnLog
from tether.testing.telemetry import ListAuditSink, RecordingTracer
from tether.types import ModelResponse, TokenUsage
from tether.types.execution import (
    Actor,
    AgentSpec,
    Answer,
    CommitUnknown,
    Entry,
    ExecutionDenied,
    ExecutionLimits,
    NewEntry,
    SessionState,
)
from tether.types.storage import SessionInit
from tether.types.telemetry import TelemetryLimits
from tether.types.usage import UsageLedger

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_engine(limits, *, audit=None, tracer=None, model=None, history=None, tools=(), **kwargs):
    return Engine(
        **fabricated_context(),
        model=model if model is not None else ScriptedModel([final()]),
        history=history if history is not None else MemoryHistory(),
        model_limits=limits,
        audit=audit,
        tracer=tracer,
        tools=tools,
        **kwargs,
    )


def spec(*, tools=()):
    return AgentSpec(
        id="fixture",
        version="1",
        title="Fixture",
        instructions="instruction marker",
        model="fixture",
        tools=tools,
    )


async def test_actual_agent_has_metadata_only_known_events_and_source_gate_context(limits):
    sink, tracer = ListAuditSink(capacity=128), RecordingTracer(id_factory(), capacity=128)
    model = ScriptedModel(
        [
            ModelResponse(
                content="response marker",
                finish_reason="stop",
                usage=TokenUsage(input_tokens=5, output_tokens=2),
            )
        ]
    )
    engine = make_engine(limits, audit=sink, tracer=tracer, model=model)
    actor = Actor(
        id="owner", principal="current-principal", attributes={"secret": "attribute marker"}
    )
    log = MemoryTurnLog(SessionState(id="session", owner=actor.id), turn_id="phase")
    notified = []
    try:
        result = await engine.run_turn(
            actor,
            log,
            "input marker",
            spec=spec(),
            accepted=lambda: notified.append(tuple(log.entries)),
        )
        assert result.stop_reason == "final"
        assert notified
    finally:
        assert await engine.aclose()
    assert engine.observers.failures == engine.observers.tracer.failures == 0
    encoded = " ".join(r.model_dump_json() for r in sink.records)
    for marker in ("instruction marker", "response marker", "input marker", "attribute marker"):
        assert marker not in encoded
    assert {
        "user_message",
        "assistant_message",
        "model_call",
        "x.model.prepared",
        "x.model.observed",
    } <= {r.kind for r in sink.records}
    assert all(r.context.principal == "current-principal" for r in sink.records)
    gates = {r.ids.span_id for r in tracer.records if r.name == "tether.gate"}
    assert all(r.context.span_id in gates for r in sink.records if r.kind == "authorization")
    call = next(r for r in tracer.records if r.name == "tether.model_call")
    assert model.requests[0].traceparent == call.ids.traceparent
    assert call.attributes["tether.input_tokens"] == 5
    assert call.attributes["tether.usage_complete"] is True
    event = next(r for r in sink.records if r.kind == "model_call")
    usage = event.detail["usage"]
    assert isinstance(usage, dict)
    assert usage["input_tokens"] == 5
    assert event.detail["usage_complete"] is True
    assert event.context.span_id == call.ids.span_id
    assert all(r.finished for r in tracer.records)
    assert all(r.parent is not None for r in tracer.records if r.name != "tether.turn")


@pytest.mark.parametrize("failure", ["unknown", "mismatch"])
async def test_unacknowledged_or_invalid_commits_emit_no_accepted_event(limits, failure):
    sink = ListAuditSink(capacity=4)
    observers = Observers(sink)
    observers.delivery.bind()

    class Log(MemoryTurnLog):
        async def append(self, entries):
            result = await super().append(entries)
            if failure == "unknown":
                raise CommitUnknown("fabricated")
            return tuple(e.model_copy(update={"kind": "wrong"}) for e in result)

    actor = Actor(id="owner")
    log = Log(SessionState(id="session", owner=actor.id))
    gov = Governance(
        actor,
        log,
        MemoryHistory(),
        make_engine(limits).policy,
        "phase",
        ExecutionLimits(),
        observers=observers,
    )
    with pytest.raises((CommitUnknown, ExecutionDenied)):
        await gov.fact("turn_ended", key="end", stop_reason="final")
    assert await observers.delivery.aclose()
    assert not sink.records


async def test_stable_retry_is_deduplicated_and_sink_cannot_mutate_journal(limits):
    class Sink(ListAuditSink):
        async def emit(self, *, context, kind, detail):
            await super().emit(context=context, kind=kind, detail=detail)
            detail["phase_turn_id"] = "mutated"

    sink = Sink(capacity=4)
    engine = make_engine(limits, audit=sink)
    engine._bind_loop()
    log = MemoryTurnLog(SessionState(id="session", owner="owner"))
    gov = engine._governance(Actor(id="owner"), log, "phase")
    entry = NewEntry(kind="turn_ended", key="end", turn_id="phase", facts={"stop_reason": "final"})
    await gov.commit([entry])
    await gov.commit([entry])
    assert await engine.aclose()
    assert len(sink.records) == 1
    assert engine.audit_stats.duplicates == 1
    assert log.entries[0].facts == {"stop_reason": "final"}


async def test_confirmation_uses_current_principal_and_separate_accepting_phase(limits):
    sink, tracer = ListAuditSink(capacity=128), RecordingTracer(id_factory(), capacity=128)
    tool = DemoTool()
    engine = make_engine(
        limits, audit=sink, tracer=tracer, tools=(tool,), model=ScriptedModel([proposal(), final()])
    )
    log = MemoryTurnLog(
        SessionState(id="session", owner="owner", approval="manual"), turn_id="source"
    )
    initial = await engine.run_turn(
        Actor(id="owner", principal="first"), log, "Fabricated", spec=spec(tools=(tool.name,))
    )
    log.turn_id = "accepting"
    result = await engine.answer(
        Actor(id="owner", principal="second"),
        log,
        initial.pending[0].invocation_id,
        Answer(approved=True, note="approval note marker"),
    )
    assert result is not None
    assert result.stop_reason == "final"
    assert await engine.aclose()
    approved = next(
        r for r in sink.records if r.kind == "confirmation" and r.detail["state"] == "approved"
    )
    assert approved.context.principal == "second"
    assert approved.detail["source_turn_id"] == "source"
    assert approved.detail["phase_turn_id"] == "accepting"
    assert "approval note marker" not in " ".join(r.model_dump_json() for r in sink.records)


async def test_hung_sink_does_not_delay_acceptance_or_retain_store_root(
    tmp_path, limits, client_limits
):
    entered, release = asyncio.Event(), asyncio.Event()

    class Sink:
        async def emit(self, **kwargs):
            entered.set()
            while not release.is_set():
                with suppress(asyncio.CancelledError):
                    await release.wait()

    root = await JsonlRoot.open(tmp_path)
    store, history = await JsonlSessionStore.open(root), await JsonlHistory.open(root)
    engine = make_engine(
        limits,
        audit=Sink(),
        history=MessageHistoryAdapter(history),
        history_provider=history,
        telemetry_limits=TelemetryLimits(
            delivery_timeout_seconds=0.01, shutdown_timeout_seconds=0.01
        ),
    )
    app = Tether(engine=engine, store=store, history=history, limits=client_limits, resume="off")
    await app.__aenter__()
    actor = Actor(id="owner")
    session = await store.create(actor.id, SessionInit())
    accepted = asyncio.Event()
    try:
        async with store.turn(session.id, actor.id, turn_id="phase") as log:
            result = await engine.run_turn(
                actor, log, "Fabricated", spec=spec(), accepted=accepted.set
            )
        assert result.stop_reason == "final"
        assert accepted.is_set()
        await entered.wait()
        assert not await app.aclose()
        assert engine.audit_stats.active
        reopened = await JsonlRoot.open(tmp_path)
        assert await reopened.aclose()
    finally:
        release.set()
        if not app.closed:
            await app.aclose()
        await asyncio.wait_for(engine.wait_observers_closed(), 1)
        if app._cleanup is not None:
            await asyncio.wait_for(app._cleanup, 1)
        assert app.drained
        assert await root.aclose()


def accepted(index, kind, **facts):
    return Entry(index=index, at=datetime.now(UTC), kind=kind, turn_id="source", facts=facts)


def test_model_metadata_uses_per_call_retry_subtotals_and_enriched_attempts():
    ledger = UsageLedger()

    def observe(index, call, attempt, usage):
        value = accepted(
            index,
            "model_receipt",
            identity={
                "session_id": "session",
                "turn_id": "source",
                "model_call_id": call,
                "attempt_id": attempt,
                "purpose": "answer",
            },
            usage=usage,
        )
        ledger.apply(value)
        return value

    scope = Scope("owner", "session", "principal", "phase")
    observe(0, "first", "a", {"input_tokens": 3, "output_tokens": 2})
    observe(1, "second", "b", {"input_tokens": 100, "output_tokens": 20})
    observe(2, "first", "c", None)
    result = record(accepted(3, "model_call", model_call_id="first"), scope, ledger, None)
    assert result is not None
    usage = result.detail["usage"]
    assert isinstance(usage, dict)
    assert usage["input_tokens"] == 3
    assert result.detail["usage_complete"] is False
    observe(4, "first", "c", {"input_tokens": 4, "output_tokens": 1, "reasoning_tokens": 1})
    last = observe(5, "first", "c", None)
    result = record(last, scope, ledger, None)
    assert result is not None
    usage = result.detail["usage"]
    assert isinstance(usage, dict)
    assert usage["input_tokens"] == 4
    assert usage["reasoning_tokens"] == 1
    total, complete = ledger.call_usage("first")
    assert complete
    assert total is not None
    assert total.input_tokens == 7
    assert ledger.call_usage("unknown") == (None, False)


def test_unknown_extension_and_arbitrary_field_or_reason_never_passes_registry():
    scope = Scope("owner", "session", "principal", "phase")
    ledger = UsageLedger()
    assert (
        record(accepted(0, "x.custom.content", text="private marker"), scope, ledger, None) is None
    )
    event = record(
        accepted(
            1,
            "authorization",
            action="private marker",
            resource="resource marker",
            reason="private marker",
            allowed=True,
            raw="private marker",
        ),
        scope,
        ledger,
        None,
    )
    assert event is not None
    assert "private marker" not in event.model_dump_json()
    assert "resource marker" not in event.model_dump_json()
    assert event.detail["action"] == event.detail["reason"] == "other"
    assert (
        record(
            accepted(2, "message", role="tool", content="private marker"),
            replace(scope, principal="other"),
            ledger,
            None,
        )
        is None
    )


async def test_custom_recorder_known_final_usage_reaches_model_span_without_claiming_complete(
    limits,
):
    tracer = RecordingTracer(id_factory(), capacity=128)
    response = ModelResponse(
        content="Fabricated",
        finish_reason="stop",
        usage=TokenUsage(input_tokens=5, output_tokens=2),
    )
    engine = make_engine(
        limits, tracer=tracer, model=ScriptedModel([response]), attempt_factory=ListAttemptRecorder
    )
    log = MemoryTurnLog(SessionState(id="session", owner="owner"), turn_id="phase")
    result = await engine.run_turn(Actor(id="owner"), log, "Fabricated", spec=spec())
    assert result.stop_reason == "final"
    assert await engine.aclose()
    call = next(r for r in tracer.records if r.name == "tether.model_call")
    assert call.attributes["tether.input_tokens"] == 5
    assert call.attributes["tether.output_tokens"] == 2
    assert call.attributes["tether.usage_complete"] is False


@pytest.mark.parametrize("finish", ["filtered", "unknown"])
def test_registry_preserves_declared_finish_reasons(finish):
    event = record(
        accepted(0, "model_call", model_call_id="call", finish_reason=finish),
        Scope("owner", "session", "principal", "phase"),
        UsageLedger(),
        None,
    )
    assert event is not None
    assert event.detail["finish_reason"] == finish


async def test_concurrent_actors_have_separate_root_and_packet_contexts(limits):
    sink, tracer = ListAuditSink(capacity=128), RecordingTracer(id_factory(), capacity=128)
    engine = make_engine(limits, audit=sink, tracer=tracer, model=ScriptedModel([final(), final()]))

    async def turn(owner):
        actor = Actor(id=owner, principal=owner + "-principal")
        log = MemoryTurnLog(
            SessionState(id="session-" + owner, owner=owner), turn_id="phase-" + owner
        )
        result = await engine.run_turn(actor, log, "Fabricated", spec=spec())
        assert result.stop_reason == "final"

    await asyncio.gather(turn("one"), turn("two"))
    assert await engine.aclose()
    roots = {
        r.ids.trace_id: r.attributes["tether.owner"]
        for r in tracer.records
        if r.name == "tether.turn"
    }
    assert set(roots.values()) == {"one", "two"}
    for packet in sink.records:
        assert packet.context.trace_id is not None
        assert roots[packet.context.trace_id] == packet.context.actor
    assert all(r.context.principal == r.context.actor + "-principal" for r in sink.records)
