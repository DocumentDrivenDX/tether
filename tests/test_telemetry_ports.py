"""Neutral telemetry defaults/doubles and reusable logical conformance."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from itertools import count
from threading import Event

import pytest
from functional_watchdog import MAX_FUNCTIONAL_WATCHDOG_MS
from pydantic import ValidationError
from selected_profiles_process import await_retained

from tether.engine.observers.delivery import AuditDelivery
from tether.ports.telemetry import NoopAuditSink, NullTracer
from tether.testing.conformance import (
    AuditSinkFixture,
    ConformanceFailure,
    TracerFixture,
    audit_sink_suite,
    tracer_suite,
)
from tether.testing.telemetry import ListAuditSink, RecordingTracer
from tether.types import JsonValue
from tether.types.telemetry import AuditContext, AuditRecord, TelemetryLimits, TraceIds


async def _wait_for_sink_entry(entered: asyncio.Event, delivery) -> None:
    while not entered.is_set():
        active = delivery._active
        if active is not None:
            waiter = asyncio.create_task(entered.wait())
            try:
                done, _ = await asyncio.wait((waiter, active), return_when=asyncio.FIRST_COMPLETED)
                if active in done and waiter not in done:
                    await active
                    raise AssertionError("audit_delivery_ended_before_sink_entry")
                await waiter
                return
            finally:
                await await_retained(asyncio.create_task(_cancel_and_join(waiter)))

        changed = delivery._changed
        changed.clear()
        if entered.is_set() or delivery._active is not None:
            continue
        if delivery._drained():
            raise AssertionError("audit_delivery_drained_before_sink_entry")
        runner = delivery._runner
        if runner is not None:
            entry_waiter = asyncio.create_task(entered.wait())
            change_waiter = asyncio.create_task(changed.wait())
            try:
                done, _ = await asyncio.wait(
                    (entry_waiter, change_waiter, runner),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if entry_waiter in done:
                    return
                if runner in done:
                    await runner
                    if change_waiter not in done:
                        await change_waiter
            finally:
                await await_retained(
                    asyncio.create_task(_cancel_and_join(entry_waiter, change_waiter))
                )
            continue
        await changed.wait()


async def _cancel_and_join(*tasks: asyncio.Task) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _wait_for_signal_before_owner_exit(
    signal: asyncio.Task, owners: tuple[asyncio.Task, ...], reason: str
) -> None:
    try:
        done, _ = await asyncio.wait((signal, *owners), return_when=asyncio.FIRST_COMPLETED)
        if signal not in done:
            for owner in owners:
                if owner in done:
                    try:
                        await owner
                    except BaseException as error:
                        raise AssertionError(reason) from error
            raise AssertionError(reason)
        await signal
    finally:
        await await_retained(asyncio.create_task(_cancel_and_join(signal)))


async def _capture_cleanup_failure(failures, operation):
    try:
        return await operation
    except BaseException as error:
        failures.append(error)


def _start_hung_sink_cleanup(release, app, engine, root) -> asyncio.Task:
    async def cleanup() -> None:
        failures = []
        release.set()
        if not app.closed:
            await _capture_cleanup_failure(failures, app.aclose())
        if app._cleanup is not None:
            await _capture_cleanup_failure(failures, app._cleanup)
        await _capture_cleanup_failure(failures, engine.wait_observers_closed())
        if not app.drained:
            failures.append(AssertionError("hung_sink_application_not_drained"))
        root_closed = await _capture_cleanup_failure(failures, root.aclose())
        if root_closed is not True:
            failures.append(AssertionError("hung_sink_root_not_closed"))
        if failures:
            raise BaseExceptionGroup("hung_sink_cleanup_failed", failures)

    return asyncio.create_task(cleanup())


def _start_worker_cleanup(
    release: Event, waiter: asyncio.Task, worker: asyncio.Task
) -> asyncio.Task:
    async def cleanup() -> None:
        release.set()
        await _cancel_and_join(waiter)
        await await_retained(worker)

    return asyncio.create_task(cleanup())


async def _finish_worker_cleanup(
    cleanup: asyncio.Task, caller: asyncio.Task | None, worker: asyncio.Task
) -> None:
    await await_retained(cleanup)
    if caller is not None:
        with suppress(asyncio.CancelledError):
            await caller
    await await_retained(worker)


def _start_worker_cleanup_finish(
    finish: Event, cleanup: asyncio.Task, caller: asyncio.Task | None, worker: asyncio.Task
) -> asyncio.Task:
    async def finalize() -> None:
        finish.set()
        await _finish_worker_cleanup(cleanup, caller, worker)

    return asyncio.create_task(finalize())


def _start_held_worker():
    loop = asyncio.get_running_loop()
    started, released = asyncio.Event(), asyncio.Event()
    release, finish = Event(), Event()

    def worker():
        loop.call_soon_threadsafe(started.set)
        release.wait()
        loop.call_soon_threadsafe(released.set)
        finish.wait()

    return started, released, release, finish, asyncio.create_task(asyncio.to_thread(worker))


def _acknowledged_shield(original_shield, acknowledgements):
    async def wrap(task):
        try:
            return await original_shield(task)
        except asyncio.CancelledError:
            acknowledgements.put_nowait(True)
            raise

    return wrap


def _start_cleanup_caller(cleanup):
    ready = asyncio.Event()

    async def wait_for_cleanup():
        ready.set()
        await await_retained(cleanup)

    return asyncio.create_task(wait_for_cleanup()), ready


async def _run_worker_cancellation_control(monkeypatch):
    started, released, release, finish, worker = _start_held_worker()
    start_waiter = asyncio.create_task(started.wait())
    cleanup = None
    caller = None
    try:
        await _wait_for_signal_before_owner_exit(
            start_waiter, (worker,), "trace_factory_worker_ended_before_entry"
        )
        cleanup = _start_worker_cleanup(release, start_waiter, worker)
        await _wait_for_signal_before_owner_exit(
            asyncio.create_task(released.wait()),
            (worker, cleanup),
            "trace_factory_worker_ended_before_cleanup_hold",
        )
        acknowledgements = asyncio.Queue()
        monkeypatch.setattr(
            asyncio, "shield", _acknowledged_shield(asyncio.shield, acknowledgements)
        )
        caller, caller_ready = _start_cleanup_caller(cleanup)
        await caller_ready.wait()
        for _ in range(2):
            caller.cancel()
            await _wait_for_signal_before_owner_exit(
                asyncio.create_task(acknowledgements.get()),
                (caller,),
                "retained_caller_ended_before_cancel_ack",
            )
            assert not caller.done()
            assert not worker.done()
    finally:
        if cleanup is None:
            cleanup = _start_worker_cleanup(release, start_waiter, worker)
        finalizer = _start_worker_cleanup_finish(finish, cleanup, caller, worker)
        await await_retained(finalizer)
    assert worker.done()
    assert cleanup.done()


def test_sink_entry_wait_reports_delivery_terminal_before_entry():
    async def scenario():
        class EmptySink:
            async def emit(self, **kwargs):
                return None

        delivery = AuditDelivery(EmptySink(), TelemetryLimits())
        delivery.bind()
        assert delivery.submit(
            AuditRecord(
                context=AuditContext(actor="owner", event_id="event"),
                kind="authorization",
                detail={},
            )
        )
        with pytest.raises(
            AssertionError, match=r"audit_delivery_(ended|drained)_before_sink_entry"
        ):
            await _wait_for_sink_entry(asyncio.Event(), delivery)
        assert await delivery.aclose()
        await delivery.wait_closed()

    asyncio.run(scenario())


def id_factory():
    sequence = count(1)

    def generate(parent):
        index = next(sequence)
        return TraceIds(
            trace_id=parent.trace_id if parent is not None else f"{index:032x}",
            span_id=f"{index:016x}",
            trace_flags=1,
        )

    return generate


@pytest.mark.parametrize("captures", [False, True])
def test_public_audit_sink_suite(captures):
    @asynccontextmanager
    async def factory():
        sink = ListAuditSink(capacity=2) if captures else NoopAuditSink()

        async def read():
            return sink.records if isinstance(sink, ListAuditSink) else ()

        yield AuditSinkFixture(sink, read, captures)

    report = asyncio.run(
        audit_sink_suite(factory, timeout_seconds=MAX_FUNCTIONAL_WATCHDOG_MS / 1000.0)
    )
    assert report.capabilities == {"captures": captures}
    assert ("audit_snapshot" in report.checks) == captures
    assert ("audit_noop_capture" in report.checks) != captures
    assert "bounded_delivery_failure_shutdown" in report.required_external


@pytest.mark.parametrize("captures", [False, True])
def test_public_tracer_suite(captures):
    @asynccontextmanager
    async def factory():
        tracer = RecordingTracer(id_factory(), capacity=8) if captures else NullTracer()

        async def read():
            return tracer.records if isinstance(tracer, RecordingTracer) else ()

        yield TracerFixture(tracer, read, captures)

    report = asyncio.run(tracer_suite(factory, timeout_seconds=MAX_FUNCTIONAL_WATCHDOG_MS / 1000.0))
    assert report.capabilities == {"captures": captures}
    assert ("trace_attributes" in report.checks) == captures
    assert ("trace_noop_capture" in report.checks) != captures
    assert "exporter_receiver_loss_shutdown" in report.required_external


@pytest.mark.parametrize(
    "values",
    [
        {"trace_id": "0" * 32, "span_id": "1" * 16},
        {"trace_id": "1" * 32, "span_id": "0" * 16},
        {"trace_id": "A" * 32, "span_id": "1" * 16},
        {"trace_id": "1" * 32, "span_id": "bad"},
        {"trace_id": "1" * 32, "span_id": "1" * 16, "trace_flags": 256},
        {"trace_id": "1" * 32, "span_id": "1" * 16, "trace_flags": True},
    ],
)
def test_invalid_trace_ids_refuse(values):
    with pytest.raises(ValidationError):
        TraceIds.model_validate(values)


@pytest.mark.parametrize(
    "values",
    [{"trace_id": "1" * 32}, {"span_id": "1" * 16}, {"trace_id": "0" * 32, "span_id": "1" * 16}],
)
def test_partial_or_invalid_audit_trace_context_refuses(values):
    with pytest.raises(ValidationError):
        AuditContext(actor="owner", event_id="event", **values)


def test_audit_capacity_size_and_reader_snapshots():
    async def scenario():
        sink = ListAuditSink(capacity=1, max_record_bytes=512)
        context = AuditContext(actor="owner", event_id="event")
        await sink.emit(context=context, kind="model_call", detail={"oversized": "x" * 1024})
        assert sink.oversized == 1
        assert sink.records == ()
        detail: dict[str, JsonValue] = {"count": 1}
        await sink.emit(context=context, kind="model_call", detail=detail)
        detail["count"] = 9
        assert sink.records[0].detail == {"count": 1}
        sink.records[0].detail["count"] = 42
        assert sink.records[0].detail == {"count": 1}
        await sink.emit(context=context, kind="model_call", detail={})
        assert sink.overflow == 1
        assert len(sink.records) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("capacity", [0, -1, True, 1.5])
def test_invalid_recording_capacity_refuses(capacity):
    with pytest.raises(ValueError, match="telemetry_capacity_invalid"):
        ListAuditSink(capacity=capacity)
    with pytest.raises(ValueError, match="telemetry_capacity_invalid"):
        RecordingTracer(id_factory(), capacity=capacity)


def test_recording_tracer_limits_and_closed_snapshots():
    tracer = RecordingTracer(id_factory(), capacity=1, max_attributes=1, max_value_bytes=32)
    with tracer.start_as_current_span("root", attributes={"count": 1}) as span:
        span.set_attribute("count", 2)
        span.set_attribute("other", True)
        span.set_attribute("count", "x" * 33)
        with pytest.raises(ValueError, match="trace_attribute_invalid"):
            span.set_attribute("invalid", float("nan"))
        with pytest.raises(ValueError, match="trace_reason_invalid"):
            span.error("fabricated exception content")
        span.error("fabricated_failure")
        with tracer.start_as_current_span("overflow") as noop:
            assert noop.ids is None
        assert tracer.current_ids() == span.ids
    record = tracer.records[0]
    assert record.finished
    assert record.attributes == {"count": 2}
    assert record.error_reason == "fabricated_failure"
    assert record.overflow == 2
    assert tracer.overflow == 1
    record.attributes["count"] = 5
    assert tracer.records[0].attributes["count"] == 2
    with pytest.raises(RuntimeError, match="span_finished"):
        span.set_attribute("count", 3)
    with pytest.raises(RuntimeError, match="span_finished"):
        span.error("failed")


def test_failed_span_creation_releases_admission_and_preserves_context():
    generate = id_factory()

    def factory(parent):
        if parent is not None:
            return TraceIds(trace_id="f" * 32, span_id="e" * 16)
        return generate(parent)

    tracer = RecordingTracer(factory, capacity=2, max_value_bytes=32)
    with (
        pytest.raises(ValueError, match="trace_name_invalid"),
        tracer.start_as_current_span("x" * 33),
    ):
        pass
    with tracer.start_as_current_span("root") as root:
        with (
            pytest.raises(ValueError, match="trace_parent_invalid"),
            tracer.start_as_current_span("child"),
        ):
            pass
        assert tracer.current_ids() == root.ids
    with tracer.start_as_current_span("next"):
        pass
    assert len(tracer.records) == 2
    assert tracer.current_ids() is None


def test_worker_admission_counts_unpublished_span():
    async def scenario():
        loop = asyncio.get_running_loop()
        started, release = asyncio.Event(), Event()

        def factory(parent):
            loop.call_soon_threadsafe(started.set)
            release.wait()
            return TraceIds(trace_id="1" * 32, span_id="1" * 16)

        tracer = RecordingTracer(factory, capacity=1)

        def worker():
            with tracer.start_as_current_span("worker"):
                pass

        task = asyncio.create_task(asyncio.to_thread(worker))
        waiter = asyncio.create_task(started.wait())
        try:
            done, _ = await asyncio.wait((waiter, task), return_when=asyncio.FIRST_COMPLETED)
            if task in done and waiter not in done:
                await task
                pytest.fail("trace_factory_worker_ended_before_entry")
            await waiter
            with tracer.start_as_current_span("overflow") as span:
                assert span.ids is None
        finally:
            cleanup_task = _start_worker_cleanup(release, waiter, task)
            await await_retained(cleanup_task)
        assert tracer.overflow == 1
        assert len(tracer.records) == 1

    asyncio.run(scenario())


def test_worker_cleanup_survives_repeated_caller_cancellation(monkeypatch):
    asyncio.run(_run_worker_cancellation_control(monkeypatch))


def test_copied_parent_context_remains_valid_after_parent_span_ends():
    from contextvars import copy_context

    tracer = RecordingTracer(id_factory(), capacity=2)
    with tracer.start_as_current_span("parent") as parent:
        captured = copy_context()

    def worker():
        assert tracer.current_ids() == parent.ids
        with tracer.start_as_current_span("late_worker"):
            pass

    captured.run(worker)
    assert tracer.records[1].parent == parent.ids
    assert tracer.current_ids() is None


def test_noop_never_invents_context_inside_recording_trace():
    tracer, noop = RecordingTracer(id_factory(), capacity=1), NullTracer()
    with tracer.start_as_current_span("root") as parent:
        with noop.start_as_current_span("noop") as span:
            assert span.ids is None
            assert span.traceparent is None
            assert noop.current_ids() is None
        assert tracer.current_ids() == parent.ids


def test_sink_suite_catches_reference_retention():
    @asynccontextmanager
    async def factory():
        from tether.types.telemetry import AuditRecord

        class Broken:
            def __init__(self):
                self.values = []

            async def emit(self, *, context, kind, detail):
                self.values.append(
                    AuditRecord.model_construct(context=context, kind=kind, detail=detail)
                )

        sink = Broken()

        async def read():
            return tuple(sink.values)

        yield AuditSinkFixture(sink, read, True)

    with pytest.raises(ConformanceFailure, match="audit_snapshot"):
        asyncio.run(audit_sink_suite(factory, timeout_seconds=MAX_FUNCTIONAL_WATCHDOG_MS / 1000.0))


def test_tracer_suite_catches_missing_context():
    @asynccontextmanager
    async def factory():
        class Broken(RecordingTracer):
            def current_ids(self):
                return None

        tracer = Broken(id_factory(), capacity=8)

        async def read():
            return tracer.records

        yield TracerFixture(tracer, read, True)

    with pytest.raises(ConformanceFailure, match="trace_root_current"):
        asyncio.run(tracer_suite(factory, timeout_seconds=MAX_FUNCTIONAL_WATCHDOG_MS / 1000.0))


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_suite_timeout_bounds_refuse(value):
    @asynccontextmanager
    async def factory():
        async def read():
            return ()

        yield TracerFixture(NullTracer(), read, False)

    async def scenario():
        with pytest.raises(ValueError, match="telemetry_suite_timeout_invalid"):
            await tracer_suite(factory, timeout_seconds=value)

    asyncio.run(scenario())


def test_trace_attributes_reject_unsupported_values_with_static_reason():
    from typing import cast

    from tether.types.telemetry import TraceScalar

    tracer = RecordingTracer(id_factory(), capacity=1)
    with tracer.start_as_current_span("root") as span:
        for value in (None, ["fabricated"], {"fabricated": "content"}, 10**5000):
            with pytest.raises(ValueError, match="trace_attribute_invalid"):
                span.set_attribute("value", cast(TraceScalar, value))
    assert not tracer.records[0].attributes


def test_audit_capacity_holds_across_simultaneous_event_loops(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor, wait
    from threading import Barrier

    from tether.types.canonical import canonical

    both_arrived = Event()
    boundary = Barrier(2, action=both_arrived.set)

    def encode(value):
        result = canonical(value)
        boundary.wait()
        return result

    monkeypatch.setattr("tether.testing.telemetry.canonical", encode)
    sink = ListAuditSink(capacity=1)
    context = AuditContext(actor="owner", event_id="event")

    def emit():
        try:
            asyncio.run(sink.emit(context=context, kind="authorization", detail={}))
        finally:
            if not both_arrived.is_set():
                boundary.abort()

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = []
        try:
            for _ in range(2):
                futures.append(workers.submit(emit))
            for future in futures:
                future.result()
        finally:
            boundary.abort()
            wait(futures)
    assert both_arrived.is_set()
    assert len(sink.records) == 1
    assert sink.overflow == 1


def test_attribute_names_require_strings():
    from typing import cast

    tracer = RecordingTracer(id_factory(), capacity=1)
    with tracer.start_as_current_span("root") as span:
        for name in (None, 123, ["fabricated"]):
            with pytest.raises(ValueError, match="trace_attribute_invalid"):
                span.set_attribute(cast(str, name), 1)
    assert not tracer.records[0].attributes


def test_tracer_suite_rejects_exception_suppression():
    from collections.abc import Generator, Mapping
    from contextlib import contextmanager

    from tether.types.telemetry import Span, TraceScalar

    @asynccontextmanager
    async def factory():
        class Suppressing(RecordingTracer):
            @contextmanager
            def start_as_current_span(
                self, name: str, *, attributes: Mapping[str, TraceScalar] | None = None
            ) -> Generator[Span, None, None]:
                with (
                    super().start_as_current_span(name, attributes=attributes) as span,
                    suppress(RuntimeError),
                ):
                    yield span

        tracer = Suppressing(id_factory(), capacity=8)

        async def read():
            return tracer.records

        yield TracerFixture(tracer, read, True)

    with pytest.raises(ConformanceFailure, match="trace_primary_exception"):
        asyncio.run(tracer_suite(factory, timeout_seconds=MAX_FUNCTIONAL_WATCHDOG_MS / 1000.0))


def test_invalid_span_names_and_reason_types_use_static_errors():
    from typing import cast

    tracer = RecordingTracer(id_factory(), capacity=1)
    for name in (None, 123, "\ud800"):
        with (
            pytest.raises(ValueError, match="trace_name_invalid"),
            tracer.start_as_current_span(cast(str, name)),
        ):
            pass
    with tracer.start_as_current_span("root") as span:
        for reason in (None, 123, ["fabricated"]):
            with pytest.raises(ValueError, match="trace_reason_invalid"):
                span.error(cast(str, reason))
    assert tracer.records[0].error_reason is None
