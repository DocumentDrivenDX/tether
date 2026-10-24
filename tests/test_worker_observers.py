"""Real task/thread lifecycle metadata stays separate from accepted tool results."""

from __future__ import annotations

import asyncio
import inspect
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from threading import Event, get_ident
from types import SimpleNamespace

import pytest
from test_engine_telemetry import make_engine, spec
from test_gated_execution import DemoTool, final, proposal
from test_guarded_tracing import BrokenTracer
from test_resume_engine import interrupted
from test_resume_engine import recovery as recovery
from test_telemetry_ports import id_factory

from tether.engine.observers.facade import Observers
from tether.engine.observers.metadata import Scope
from tether.engine.workers import Workers
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ScriptedModel
from tether.testing.execution import MemoryTurnLog
from tether.testing.telemetry import ListAuditSink, RecordingTracer
from tether.types.execution import Actor, Answer, CommitUnknown, ExecutionDenied, SessionState
from tether.types.telemetry import TelemetryLimits

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def observation(observers, *, principal="current", intent: int | None = 4):
    return observers.worker(
        scope=Scope("owner", "session", principal, "accepting", "spec", "1"),
        source_turn_id="source",
        source_position=3,
        call_position=0,
        invocation_id="invocation",
        intent_position=intent,
    )


def phases(sink):
    return {r.kind.removeprefix("x.executor."): r for r in sink.records}


@pytest.mark.parametrize("synchronous", [True, False])
async def test_actual_body_parent_context_and_copied_metadata(synchronous):
    sink, tracer = ListAuditSink(capacity=32), RecordingTracer(id_factory(), capacity=32)
    observers, workers = Observers(sink, tracer), Workers(1)
    observers.delivery.bind()
    caller_thread = get_ident()
    contexts = []

    def sync_body():
        contexts.append((get_ident(), tracer.current_ids()))
        return "result content marker"

    async def async_body():
        contexts.append((get_ident(), tracer.current_ids()))
        await asyncio.sleep(0)
        return "result content marker"

    with observers.tracer.start_as_current_span("parent") as parent:
        selected = observation(observers)
        assert selected is not None
        assert (
            await workers.call(
                "session", sync_body if synchronous else async_body, observe=selected
            )
            == "result content marker"
        )
        assert tracer.current_ids() == parent.ids
    assert await workers.close(1)
    assert await observers.delivery.aclose(deadline=asyncio.get_running_loop().time() + 1)
    rows = phases(sink)
    assert set(rows) == {"entered", "exited", "completed"}
    assert rows["exited"].detail["outcome"] == "returned"
    assert rows["completed"].detail["body_entered"] is True
    assert all(r.context.principal == "current" for r in rows.values())
    assert all(r.detail["intent_position"] == 4 for r in rows.values())
    assert "result content marker" not in " ".join(r.model_dump_json() for r in rows.values())
    body = next(
        r for r in tracer.records if r.name in {"tether.executor_function", "tether.async_body"}
    )
    wait = next(r for r in tracer.records if r.name == "tether.tool_call")
    assert body.parent == wait.ids
    assert wait.parent == parent.ids
    assert contexts[0][1] == body.ids
    assert (contexts[0][0] != caller_thread) == synchronous
    assert all(r.finished for r in tracer.records)


def assert_abandoned_span_lifetime(tracer):
    wait = next(r for r in tracer.records if r.name == "tether.tool_call")
    body = next(
        r for r in tracer.records if r.name in {"tether.executor_function", "tether.async_body"}
    )
    assert wait.finished
    assert wait.error_reason == "cancelled"
    assert not body.finished


@pytest.mark.parametrize("synchronous", [True, False])
@pytest.mark.parametrize("fail", [True, False])
async def test_abandoned_wait_and_late_body_exit_are_distinct(synchronous, fail):
    sink, tracer = ListAuditSink(capacity=32), RecordingTracer(id_factory(), capacity=32)
    observers, workers = Observers(sink, tracer), Workers(1)
    observers.delivery.bind()
    entered, release = asyncio.Event(), asyncio.Event()
    thread_release = Event()
    loop = asyncio.get_running_loop()

    def sync_body():
        loop.call_soon_threadsafe(entered.set)
        assert thread_release.wait(2)
        if fail:
            raise ValueError("late exception content marker")
        return "late result content marker"

    async def async_body():
        entered.set()
        while not release.is_set():
            with suppress(asyncio.CancelledError):
                await release.wait()
        if fail:
            raise ValueError("late exception content marker")
        return "late result content marker"

    caller = asyncio.create_task(
        workers.call(
            "session", sync_body if synchronous else async_body, observe=observation(observers)
        )
    )
    try:
        await entered.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(caller, 0.5)
        assert workers.busy("session")
        assert_abandoned_span_lifetime(tracer)
        assert not await workers.close(0.01)
    finally:
        release.set()
        thread_release.set()
        await asyncio.gather(*tuple(workers.active), return_exceptions=True)
        assert await workers.close(1)
        assert await observers.delivery.aclose(deadline=loop.time() + 1)
    rows = phases(sink)
    assert set(rows) == {"entered", "outstanding", "exited", "completed"}
    assert rows["outstanding"].detail["waiter_abandoned"] is True
    assert rows["completed"].detail["waiter_abandoned"] is True
    assert rows["completed"].detail["outcome"] == ("raised" if fail else "returned")
    assert "content marker" not in " ".join(r.model_dump_json() for r in rows.values())


async def test_raw_exit_precedes_returned_live_awaitable_completion():
    sink, tracer = ListAuditSink(capacity=32), RecordingTracer(id_factory(), capacity=32)
    observers, workers = Observers(sink, tracer), Workers(1)
    observers.delivery.bind()
    release, entered = asyncio.Event(), asyncio.Event()

    async def child():
        entered.set()
        await release.wait()
        return "fabricated"

    returned = asyncio.create_task(child())
    caller = asyncio.create_task(
        workers.call("session", lambda: returned, observe=observation(observers))
    )
    try:
        await entered.wait()
        # One loop rendezvous settles the raw executor callbacks, not the child.
        raw_exited = asyncio.Event()

        def completed_raw(future):
            raw_exited.set()

        for future in tuple(workers.executors):
            future.add_done_callback(completed_raw)
        if workers.executors:
            await raw_exited.wait()
        await asyncio.sleep(0)
        assert workers.busy("session")
        body = next(r for r in tracer.records if r.name == "tether.executor_function")
        assert body.finished
        assert not next(r for r in tracer.records if r.name == "tether.tool_call").finished
    finally:
        release.set()
        assert await caller == "fabricated"
        assert await workers.close(1)
        assert await observers.delivery.aclose(deadline=asyncio.get_running_loop().time() + 1)
    rows = phases(sink)
    assert rows["exited"].detail["returned_awaitable"] is True
    assert rows["completed"].detail["returned_awaitable"] is True


async def test_refused_admission_and_queued_cancel_never_claim_body_entry(monkeypatch):
    sink = ListAuditSink(capacity=32)
    observers, workers = Observers(sink), Workers(1)
    observers.delivery.bind()
    loop = asyncio.get_running_loop()
    pool = ThreadPoolExecutor(max_workers=1)
    barrier = Event()
    occupied = pool.submit(barrier.wait, 2)
    original = loop.run_in_executor
    monkeypatch.setattr(
        loop, "run_in_executor", lambda executor, function, *args: original(pool, function, *args)
    )
    called = []
    caller = asyncio.create_task(
        workers.call("session", lambda: called.append(True), observe=observation(observers))
    )
    try:
        await asyncio.sleep(0)
        with pytest.raises(ExecutionDenied, match="worker_capacity"):
            await workers.call(
                "session", lambda: called.append(True), observe=observation(observers)
            )
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
    finally:
        barrier.set()
        await asyncio.gather(*tuple(workers.active), return_exceptions=True)
        monkeypatch.setattr(loop, "run_in_executor", original)
        await asyncio.to_thread(pool.shutdown)
        assert occupied.done()
        assert await workers.close(1)
        assert await observers.delivery.aclose(deadline=loop.time() + 1)
    assert not called
    assert "entered" not in phases(sink)
    assert "exited" not in phases(sink)
    assert "refused" in phases(sink)
    assert "completed" in phases(sink)


@pytest.mark.parametrize("failure", ["start", "enter", "exit", "ids", "error", "suppress"])
async def test_tracer_failures_cannot_replace_body_failure(failure):
    observers, workers = Observers(tracer=BrokenTracer(failure)), Workers(1)

    async def broken_body():
        raise ValueError("primary failure marker")

    with pytest.raises(ValueError, match="primary failure marker"):
        await workers.call("session", broken_body, observe=observation(observers))
    assert await workers.close(1)


async def test_actual_agent_worker_capture_after_intent_with_current_principal(limits):
    sink = ListAuditSink(capacity=128)
    tool = DemoTool()
    engine = make_engine(
        limits, audit=sink, tools=(tool,), model=ScriptedModel([proposal(), final()])
    )
    log = MemoryTurnLog(
        SessionState(id="session", owner="owner", approval="manual"), turn_id="source"
    )
    try:
        initial = await engine.run_turn(
            Actor(id="owner", principal="first"), log, "Fabricated", spec=spec(tools=(tool.name,))
        )
        assert not any(r.kind.startswith("x.executor.") for r in sink.records)
        log.turn_id = "accepting"
        result = await engine.answer(
            Actor(id="owner", principal="second"),
            log,
            initial.pending[0].invocation_id,
            Answer(approved=True),
        )
        assert result is not None
        assert result.stop_reason == "final"
    finally:
        assert await engine.aclose()
    intent = next(e for e in log.entries if e.key is not None and e.key.startswith("intent:"))
    rows = [r for r in sink.records if r.kind.startswith("x.executor.")]
    assert {r.kind for r in rows} == {
        "x.executor.entered",
        "x.executor.exited",
        "x.executor.completed",
    }
    assert all(r.detail["intent_position"] == intent.index for r in rows)
    assert all(r.detail["source_turn_id"] == "source" for r in rows)
    assert all(r.detail["phase_turn_id"] == "accepting" for r in rows)
    assert all(r.context.principal == "second" for r in rows)
    attempts = [r.detail["worker_attempt_id"] for r in rows]
    assert isinstance(attempts[0], str)
    assert all(a == attempts[0] for a in attempts)


async def test_no_observer_builds_no_descriptor():
    observers = Observers()
    assert observation(observers) is None
    assert inspect.iscoroutinefunction(Workers.call)


@pytest.mark.parametrize("unknown", [True, False])
async def test_required_intent_without_known_acceptance_never_builds_worker_event(limits, unknown):
    class Log(MemoryTurnLog):
        async def append(self, entries):
            accepted = await super().append(entries)
            if any(e.key is not None and e.key.startswith("intent:") for e in entries):
                if unknown:
                    raise CommitUnknown("fabricated unknown intent")
                return tuple(e.model_copy(update={"kind": "wrong"}) for e in accepted)
            return accepted

    sink, tool = ListAuditSink(capacity=128), DemoTool()
    engine = make_engine(
        limits, audit=sink, tools=(tool,), model=ScriptedModel([proposal(), final()])
    )
    log = Log(SessionState(id="session", owner="owner", approval="auto"), turn_id="phase")
    try:
        if unknown:
            with pytest.raises(CommitUnknown):
                await engine.run_turn(
                    Actor(id="owner"), log, "Fabricated", spec=spec(tools=(tool.name,))
                )
        else:
            result = await engine.run_turn(
                Actor(id="owner"), log, "Fabricated", spec=spec(tools=(tool.name,))
            )
            assert result.stop_reason == "model_denied"
    finally:
        assert await engine.aclose()
    assert not tool.calls
    assert not any(r.kind.startswith("x.executor.") for r in sink.records)


async def test_no_intent_and_distinct_dispatch_identity_with_same_invocation():
    sink, workers = ListAuditSink(capacity=32), Workers(1)
    observers = Observers(sink)
    observers.delivery.bind()
    first = observation(observers, principal="first", intent=None)
    second = observation(observers, principal="second", intent=None)
    assert first is not None
    assert second is not None
    assert await workers.call("session", lambda: "fabricated", observe=first) == "fabricated"
    assert await workers.call("session", lambda: "fabricated", observe=second) == "fabricated"
    assert await workers.close(1)
    assert await observers.delivery.aclose()
    rows = [r for r in sink.records if r.kind == "x.executor.completed"]
    assert len(rows) == 2
    assert rows[0].context.principal == "first"
    assert rows[1].context.principal == "second"
    assert rows[0].detail["worker_attempt_id"] != rows[1].detail["worker_attempt_id"]
    assert rows[0].context.event_id != rows[1].context.event_id
    assert all(r.detail["intent_position"] is None for r in rows)
    assert all(r.detail["source_position"] == 3 for r in rows)


async def test_packet_admission_failure_is_secondary(monkeypatch):
    observers, workers = Observers(ListAuditSink(capacity=4)), Workers(1)

    def broken(record):
        raise asyncio.CancelledError("observer failure marker")

    monkeypatch.setattr(observers.delivery, "submit", broken)
    selected = observation(observers)
    assert await workers.call("session", lambda: "fabricated", observe=selected) == "fabricated"
    assert await workers.close(1)
    assert observers.tracer.failures == 3


async def test_late_exit_after_observer_seal_is_counted_and_cannot_hold_root(tmp_path):
    from tether.stores.jsonl import JsonlRoot

    root = await JsonlRoot.open(tmp_path)
    observers, workers = Observers(ListAuditSink(capacity=32)), Workers(1)
    observers.delivery.bind()
    workers.scopes["session"] = root.workers
    entered, release = asyncio.Event(), asyncio.Event()

    async def child():
        entered.set()
        while not release.is_set():
            with suppress(asyncio.CancelledError):
                await release.wait()
        return "fabricated"

    caller = asyncio.create_task(workers.call("session", child, observe=observation(observers)))
    try:
        await entered.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert await observers.delivery.aclose()
        assert root.workers.busy("session")
    finally:
        release.set()
        await asyncio.gather(*tuple(workers.active), return_exceptions=True)
        assert await root.aclose()
    assert observers.delivery.stats.dropped_closed == 2
    replacement = await JsonlRoot.open(tmp_path)
    assert await replacement.aclose()


def observed_recovery(h, sink, *, tool=None, model=None, selected_spec=None):
    tool = tool if tool is not None else DemoTool()
    tool.replay = "safe"
    model = model if model is not None else ScriptedModel([proposal(), final()])
    selected_spec = selected_spec if selected_spec is not None else spec(tools=(tool.name,))
    engine = make_engine(
        h.limits,
        audit=sink,
        tools=(tool,),
        model=model,
        history=MessageHistoryAdapter(h.provider),
        history_provider=h.provider,
        specs=(selected_spec,),
    )
    h.engines.append(engine)
    return SimpleNamespace(engine=engine, tool=tool, model=model, spec=selected_spec)


async def test_safe_replay_retains_original_intent_and_changes_dispatch_id(recovery, monkeypatch):
    h, sink = recovery, ListAuditSink(capacity=128)
    run = observed_recovery(h, sink)
    sid = await interrupted(h, run, "result", monkeypatch)
    assert await run.engine.aclose()
    fresh = observed_recovery(h, sink, tool=run.tool, model=run.model, selected_spec=run.spec)
    async with h.store.turn(sid, h.actor.id, turn_id="continued") as log:
        result = await fresh.engine.resume(h.actor, log)
    assert result.stop_reason == "final"
    assert len(run.tool.calls) == 2
    assert await fresh.engine.aclose()
    rows = [r for r in sink.records if r.kind == "x.executor.completed"]
    assert len(rows) == 2
    assert rows[0].detail["invocation_id"] == rows[1].detail["invocation_id"]
    assert rows[0].detail["intent_position"] == rows[1].detail["intent_position"]
    assert isinstance(rows[0].detail["intent_position"], int)
    assert rows[0].detail["worker_attempt_id"] != rows[1].detail["worker_attempt_id"]
    assert rows[0].detail["phase_turn_id"] == "original"
    assert rows[1].detail["phase_turn_id"] == "continued"
    assert all(r.detail["source_turn_id"] == "original" for r in rows)


async def test_safe_read_only_agent_dispatch_has_no_invented_intent(limits):
    sink, tool = ListAuditSink(capacity=128), DemoTool()
    tool.effect, tool.replay = "read_only", "safe"
    engine = make_engine(
        limits, audit=sink, tools=(tool,), model=ScriptedModel([proposal(), final()])
    )
    log = MemoryTurnLog(SessionState(id="session", owner="owner", approval="auto"), turn_id="phase")
    try:
        result = await engine.run_turn(
            Actor(id="owner"), log, "Fabricated", spec=spec(tools=(tool.name,))
        )
        assert result.stop_reason == "final"
    finally:
        assert await engine.aclose()
    rows = [r for r in sink.records if r.kind.startswith("x.executor.")]
    assert rows
    assert all(r.detail["intent_position"] is None for r in rows)
    source = next(e for e in log.entries if e.call_manifest)
    assert all(r.detail["source_position"] == source.index for r in rows)
    assert len(tool.calls) == 1


async def test_worker_exit_releases_root_while_observational_sink_is_still_running(tmp_path):
    from tether.stores.jsonl import JsonlRoot

    emitting, sink_release = asyncio.Event(), asyncio.Event()

    class Sink:
        async def emit(self, **kwargs):
            emitting.set()
            while not sink_release.is_set():
                with suppress(asyncio.CancelledError):
                    await sink_release.wait()

    root = await JsonlRoot.open(tmp_path)
    observers = Observers(
        Sink(), limits=TelemetryLimits(delivery_timeout_seconds=0.01, shutdown_timeout_seconds=0.01)
    )
    workers = Workers(1)
    workers.scopes["session"] = root.workers
    observers.delivery.bind()
    entered, release = asyncio.Event(), asyncio.Event()

    async def child():
        entered.set()
        while not release.is_set():
            with suppress(asyncio.CancelledError):
                await release.wait()
        return "fabricated"

    caller = asyncio.create_task(workers.call("session", child, observe=observation(observers)))
    try:
        await entered.wait()
        await emitting.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        release.set()
        await asyncio.gather(*tuple(workers.active), return_exceptions=True)
        assert await root.aclose()
        assert not await observers.delivery.aclose()
        assert observers.delivery.stats.active
        replacement = await JsonlRoot.open(tmp_path)
        assert await replacement.aclose()
    finally:
        release.set()
        sink_release.set()
        await asyncio.wait_for(observers.delivery.wait_closed(), 1)
        assert await root.aclose()


async def test_async_task_cancelled_before_entry_makes_no_body_claim(monkeypatch):
    sink, workers = ListAuditSink(capacity=16), Workers(1)
    observers = Observers(sink)
    observers.delivery.bind()
    original = asyncio.ensure_future
    called = []

    def cancel_before_entry(value, *, loop):
        task = original(value, loop=loop)
        task.cancel()
        return task

    async def child():
        called.append(True)
        return "fabricated"

    monkeypatch.setattr(asyncio, "ensure_future", cancel_before_entry)
    with pytest.raises(asyncio.CancelledError):
        await workers.call("session", child, observe=observation(observers))
    monkeypatch.setattr(asyncio, "ensure_future", original)
    assert await workers.close(1)
    assert await observers.delivery.aclose()
    assert not called
    rows = phases(sink)
    assert set(rows) == {"completed"}
    assert rows["completed"].detail["body_entered"] is False
    assert rows["completed"].detail["outcome"] == "cancelled"


async def test_submission_failure_is_completion_without_physical_entry(monkeypatch):
    sink, workers = ListAuditSink(capacity=16), Workers(1)
    observers = Observers(sink)
    observers.delivery.bind()

    def refuse(executor, function, *args):
        raise RuntimeError("submission content marker")

    monkeypatch.setattr(asyncio.get_running_loop(), "run_in_executor", refuse)
    with pytest.raises(RuntimeError, match="submission content marker"):
        await workers.call("session", lambda: "fabricated", observe=observation(observers))
    assert await workers.close(1)
    assert await observers.delivery.aclose()
    rows = phases(sink)
    assert set(rows) == {"completed"}
    assert rows["completed"].detail["body_entered"] is False
    assert "submission content marker" not in rows["completed"].model_dump_json()


async def test_descriptor_construction_failure_has_no_execution_authority(monkeypatch):
    def broken():
        raise RuntimeError("fabricated identifier failure")

    monkeypatch.setattr("tether.engine.observers.facade.uuid4", broken)
    observers, workers = Observers(ListAuditSink(capacity=4)), Workers(1)
    assert observation(observers) is None
    assert observers.failures == 1
    assert (
        await workers.call("session", lambda: "fabricated", observe=observation(observers))
        == "fabricated"
    )
    assert await workers.close(1)
