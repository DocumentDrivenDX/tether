"""Real Agent mediated reads preserve policy acceptance and content-free waiter metadata."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from threading import Event, get_ident

import pytest
from test_client_resume import FreshCredential
from test_gated_execution import DemoTool, Harness, MutablePolicy
from test_guarded_tracing import BrokenTracer
from test_telemetry_ports import id_factory

from tether.engine.providers import BaseProvider
from tether.testing.telemetry import ListAuditSink, RecordingTracer
from tether.types.canonical import digest
from tether.types.execution import (
    Answer,
    CommitUnknown,
    Effect,
    ExecutionDenied,
    ExecutionLimits,
    Replay,
    RetrievalDenied,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class ReadTool(DemoTool):
    effect: Effect = "read_only"
    replay: Replay = "safe"

    def __init__(self, fetch):
        super().__init__()
        self.fetch = fetch
        self.caught = []

    async def run(self, ctx, args):
        self.calls.append(ctx.invocation_id)
        try:
            return await ctx.read("resource content marker", self.fetch)
        except BaseException as error:
            self.caught.append(error)
            raise


def observers():
    return ListAuditSink(capacity=256), RecordingTracer(id_factory(), capacity=256)


def retrievals(sink):
    return [r for r in sink.records if r.kind == "retrieval"]


@pytest.mark.parametrize("mode", ["sync", "async", "returned"])
async def test_agent_read_awaits_supported_fetch_and_correlates_exact_gate(limits, mode):
    sink, tracer = observers()
    contexts = []
    caller_thread = get_ident()

    def fetch():
        contexts.append((get_ident(), tracer.current_ids()))
        return "result content marker"

    async def async_fetch():
        return fetch()

    def returned():
        return async_fetch()

    tool = ReadTool({"sync": fetch, "async": async_fetch, "returned": returned}[mode])
    h = Harness(limits, tool=tool, audit=sink, tracer=tracer)
    credential = FreshCredential()
    h.actor = h.actor.model_copy(update={"principal": "current", "credential": credential})
    h.log.turn_id = "phase"
    result = await h.run()
    assert result.stop_reason == "final"
    assert await h.engine.aclose()
    rows = retrievals(sink)
    assert len(rows) == 1
    row = rows[0]
    gate = next(
        e for e in h.log.entries if e.kind == "authorization" and e.facts["action"] == "retrieve"
    )
    source = next(e for e in h.log.entries if e.call_manifest)
    assert row.detail["authorization_position"] == gate.index
    assert row.detail["source_position"] == source.index
    assert row.detail["invocation_id"] == tool.calls[0]
    assert row.detail["phase_turn_id"] == "phase"
    assert row.detail["outcome"] == "returned"
    assert row.detail["resource_digest"] == digest("resource content marker")
    assert "entry_position" not in row.detail
    assert row.context.principal == "current"
    span = next(r for r in tracer.records if r.name == "tether.retrieval")
    body = next(r for r in tracer.records if r.name == "tether.async_body")
    assert span.parent == body.ids
    assert span.ids.span_id == row.context.span_id
    assert contexts[0][1] == span.ids
    assert (contexts[0][0] != caller_thread) is (mode == "sync")
    assert "content marker" not in repr(sink.records) + repr(tracer.records)


async def test_known_policy_denial_has_typed_error_and_no_fetch_span(limits):
    calls = []
    tool = ReadTool(lambda: calls.append(True))
    policy = MutablePolicy()
    policy.deny.add("retrieve")
    sink, tracer = observers()
    h = Harness(limits, tool=tool, policy=policy, audit=sink, tracer=tracer)
    await h.run()
    assert await h.engine.aclose()
    assert not calls
    assert len(tool.caught) == 1
    assert isinstance(tool.caught[0], RetrievalDenied)
    assert not retrievals(sink)
    assert not any(r.name == "tether.retrieval" for r in tracer.records)
    gates = [
        r for r in sink.records if r.kind == "authorization" and r.detail["action"] == "retrieve"
    ]
    assert len(gates) == 1
    assert gates[0].detail["allowed"] is False


@pytest.mark.parametrize("fault", ["unknown", "mismatch", "store_denied"])
async def test_failed_gate_acceptance_never_fetches_or_becomes_policy_denial(
    limits, monkeypatch, fault
):
    calls = []
    tool = ReadTool(lambda: calls.append(True))
    sink, tracer = observers()
    h = Harness(limits, tool=tool, audit=sink, tracer=tracer)
    append = h.log.append

    async def fail(entries):
        if any(e.kind == "authorization" and e.facts["action"] == "retrieve" for e in entries):
            if fault == "unknown":
                raise CommitUnknown("unknown content marker")
            if fault == "store_denied":
                raise ExecutionDenied("store_scope")
            accepted = await append(entries)
            return tuple(e.model_copy(update={"kind": "wrong"}) for e in accepted)
        return await append(entries)

    monkeypatch.setattr(h.log, "append", fail)
    await h.run()
    assert await h.engine.aclose()
    assert not calls
    assert len(tool.caught) == 1
    assert not isinstance(tool.caught[0], RetrievalDenied)
    assert isinstance(tool.caught[0], CommitUnknown if fault == "unknown" else ExecutionDenied)
    assert not retrievals(sink)
    assert not any(r.name == "tether.retrieval" for r in tracer.records)


async def test_fetch_execution_denied_is_not_a_policy_denial(limits):
    def fetch():
        raise ExecutionDenied("fetch_failure_content_marker")

    tool = ReadTool(fetch)
    sink, tracer = observers()
    h = Harness(limits, tool=tool, audit=sink, tracer=tracer)
    await h.run()
    assert await h.engine.aclose()
    assert type(tool.caught[0]) is ExecutionDenied
    assert retrievals(sink)[0].detail["outcome"] == "raised"
    assert "fetch_failure_content_marker" not in repr(sink.records) + repr(tracer.records)


async def test_nested_async_reads_work_with_one_physical_worker(limits):
    class Nested(ReadTool):
        async def run(self, ctx, args):
            async def outer():
                return await ctx.read("inner resource marker", lambda: "nested result marker")

            return await ctx.read("outer resource marker", outer)

    sink, tracer = observers()
    h = Harness(
        limits, tool=Nested(None), audit=sink, tracer=tracer, limits=ExecutionLimits(max_workers=1)
    )
    assert (await asyncio.wait_for(h.run(), 2)).stop_reason == "final"
    assert await h.engine.aclose()
    rows = retrievals(sink)
    assert len(rows) == 2
    assert len({str(r.detail["retrieval_attempt_id"]) for r in rows}) == 2
    assert len({str(r.detail["authorization_position"]) for r in rows}) == 2
    assert "resource marker" not in repr(sink.records) + repr(tracer.records)


@pytest.mark.parametrize("sync", [True, False])
async def test_cancelled_read_wait_does_not_claim_fetch_exit(limits, sync):
    sink, tracer = observers()
    entered, release = asyncio.Event(), asyncio.Event()
    thread_release = Event()
    loop = asyncio.get_running_loop()

    def fetch():
        loop.call_soon_threadsafe(entered.set)
        assert thread_release.wait(3)
        return "late result marker"

    async def async_fetch():
        entered.set()
        while not release.is_set():
            with suppress(asyncio.CancelledError):
                await release.wait()
        return "late result marker"

    tool = ReadTool(fetch if sync else async_fetch)
    h = Harness(limits, tool=tool, audit=sink, tracer=tracer)
    task = asyncio.create_task(h.run())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert h.engine.workers.busy(h.log.session.id)
        span = next(r for r in tracer.records if r.name == "tether.retrieval")
        assert span.finished
        assert span.error_reason == "cancelled"
    finally:
        thread_release.set()
        release.set()
        await asyncio.gather(*tuple(h.engine.workers.active), return_exceptions=True)
        assert await h.engine.aclose()
    rows = retrievals(sink)
    assert len(rows) == 1
    assert rows[0].detail["outcome"] == "cancelled"
    assert all("exited" not in r.detail and "completed" not in r.detail for r in rows)
    assert "late result marker" not in repr(sink.records) + repr(tracer.records)


async def test_approval_continuation_read_uses_current_principal_and_phase(limits):
    sink, tracer = observers()
    tool = ReadTool(lambda: "Fabricated")
    h = Harness(limits, tool=tool, manual=True, audit=sink, tracer=tracer)
    h.actor = h.actor.model_copy(update={"principal": "first"})
    h.log.turn_id = "source"
    initial = await h.run()
    assert not retrievals(sink)
    h.actor = h.actor.model_copy(update={"principal": "second"})
    h.log.turn_id = "continuation"
    result = await h.engine.answer(
        h.actor, h.log, initial.pending[0].invocation_id, Answer(approved=True)
    )
    assert result is not None
    assert result.stop_reason == "final"
    assert await h.engine.aclose()
    row = retrievals(sink)[0]
    assert row.context.principal == "second"
    assert row.detail["source_turn_id"] == "source"
    assert row.detail["phase_turn_id"] == "continuation"


@pytest.mark.parametrize("failure", ["start", "enter", "exit", "ids", "error", "suppress"])
async def test_broken_tracer_cannot_replace_primary_fetch_failure(limits, failure):
    tool = ReadTool(lambda: (_ for _ in ()).throw(ValueError("fetch content marker")))
    h = Harness(limits, tool=tool, tracer=BrokenTracer(failure))
    await h.run()
    assert await h.engine.aclose()
    assert len(tool.caught) == 1
    assert isinstance(tool.caught[0], ValueError)


async def test_retrieval_observation_never_inspects_opaque_fetched_value(limits):
    class Opaque:
        def __repr__(self):
            raise AssertionError("unexpected repr")

        def __str__(self):
            raise AssertionError("unexpected str")

        def __len__(self):
            raise AssertionError("unexpected len")

    value = Opaque()

    class Tool(ReadTool):
        async def run(self, ctx, args):
            assert await ctx.read("opaque", lambda: value) is value
            return "Fabricated"

    sink, tracer = observers()
    h = Harness(limits, tool=Tool(None), audit=sink, tracer=tracer)
    assert (await h.run()).stop_reason == "final"
    assert await h.engine.aclose()
    assert retrievals(sink)[0].detail["outcome"] == "returned"


@pytest.mark.parametrize("target", ["uuid4", "digest", "AuditRecord"])
async def test_step_metadata_construction_failure_is_secondary(limits, monkeypatch, target):
    from tether.engine.observers import steps

    def failed(*args, **kwargs):
        raise asyncio.CancelledError("observer marker")

    monkeypatch.setattr(steps, target, failed)
    sink, tracer = observers()
    h = Harness(
        limits,
        tool=ReadTool(lambda: "Fabricated"),
        providers=(BaseProvider(),),
        audit=sink,
        tracer=tracer,
    )
    assert (await h.run()).stop_reason == "final"
    assert await h.engine.aclose()
    assert h.engine.observation_failures > 0
    assert "observer marker" not in repr(sink.records) + repr(tracer.records)
