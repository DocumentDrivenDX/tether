"""Reusable logical telemetry conformance, with explicit external-proof obligations."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

from tether.testing.telemetry import TraceObservation
from tether.types import JsonValue
from tether.types.telemetry import AuditContext, AuditRecord, AuditSink, TraceIds, Tracer

from .types import SuiteReport, require


@dataclass(frozen=True)
class AuditSinkFixture:
    sink: AuditSink
    read: Callable[[], Awaitable[tuple[AuditRecord, ...]]]
    captures: bool


@dataclass(frozen=True)
class TracerFixture:
    tracer: Tracer
    read: Callable[[], Awaitable[tuple[TraceObservation, ...]]]
    captures: bool


AuditSinkFactory = Callable[[], AbstractAsyncContextManager[AuditSinkFixture]]
TracerFactory = Callable[[], AbstractAsyncContextManager[TracerFixture]]


def timeout(value: float) -> float:
    if not 0 < value < float("inf"):
        raise ValueError("telemetry_suite_timeout_invalid")
    return value


async def audit_sink_suite(
    factory: AuditSinkFactory, *, timeout_seconds: float = 10
) -> SuiteReport:
    async with asyncio.timeout(timeout(timeout_seconds)), factory() as fixture:
        require(not await fixture.read(), "audit_fresh_fixture")
        first = AuditContext(
            actor="fabricated-owner",
            principal="fabricated-principal",
            event_id="fixture-1",
            session_id="fabricated-session",
        )
        traced = first.model_copy(
            update={"event_id": "fixture-2", "trace_id": "1" * 32, "span_id": "2" * 16}
        )
        detail: dict[str, JsonValue] = {"phase": "accepted", "count": 1}
        await fixture.sink.emit(context=first, kind="authorization", detail=detail)
        await fixture.sink.emit(context=traced, kind="model_call", detail={"input_tokens": 0})
        detail["count"] = 999
        records = await fixture.read()
        if fixture.captures:
            require(len(records) == 2, "audit_capture_count")
            require(
                records[0]
                == AuditRecord(
                    context=first, kind="authorization", detail={"phase": "accepted", "count": 1}
                ),
                "audit_snapshot",
            )
            require(
                records[1]
                == AuditRecord(context=traced, kind="model_call", detail={"input_tokens": 0}),
                "audit_trace_passthrough",
            )
            records[0].detail["count"] = 123
            require((await fixture.read())[0].detail["count"] == 1, "audit_read_isolation")
        else:
            require(not records, "audit_noop_capture")
    return SuiteReport(
        suite="audit_sink",
        capabilities={"captures": fixture.captures},
        checks=("audit_fresh_fixture",)
        + (
            (
                "audit_capture_count",
                "audit_snapshot",
                "audit_read_isolation",
                "audit_trace_passthrough",
            )
            if fixture.captures
            else ("audit_noop_capture",)
        ),
        required_external=(
            "engine_content_registry",
            "bounded_delivery_failure_shutdown",
            "exporter_receiver_loss_duplicates",
        ),
    )


async def _nest(fixture: TracerFixture) -> None:
    tracer = fixture.tracer
    require(tracer.current_ids() is None, "trace_initial_context")
    with tracer.start_as_current_span("tether.test.root", attributes={"tether.count": 1}) as root:
        ids = root.ids
        require((ids is not None) == fixture.captures, "trace_conditional_ids")
        require(tracer.current_ids() == ids, "trace_root_current")
        require(root.traceparent == (ids.traceparent if ids is not None else None), "traceparent")
        await asyncio.sleep(0)
        tracer.set_current_attribute("tether.awaited", True)
        with tracer.start_as_current_span("tether.test.child") as child:
            require(tracer.current_ids() == child.ids, "trace_child_current")
            if ids is not None:
                require(
                    child.ids is not None
                    and child.ids.trace_id == ids.trace_id
                    and child.ids.span_id != ids.span_id,
                    "trace_child_parent",
                )
            child.error("fabricated_failure")
        require(tracer.current_ids() == ids, "trace_parent_restored")

        def worker() -> None:
            require(tracer.current_ids() == ids, "trace_worker_copy")
            with tracer.start_as_current_span("tether.test.worker") as span:
                span.set_attribute("tether.worker", True)
            require(tracer.current_ids() == ids, "trace_worker_reset")

        await asyncio.to_thread(worker)
        require(tracer.current_ids() == ids, "trace_worker_isolation")
    require(tracer.current_ids() is None, "trace_final_context")


async def _concurrent(fixture: TracerFixture) -> None:
    ready = [asyncio.Event(), asyncio.Event()]
    ids: list[TraceIds | None] = []

    async def run(index: int) -> None:
        with fixture.tracer.start_as_current_span("tether.test.concurrent." + str(index)) as span:
            own = span.ids
            ids.append(own)
            ready[index].set()
            await ready[1 - index].wait()
            require(fixture.tracer.current_ids() == own, "trace_concurrent_isolation")

    await asyncio.gather(run(0), run(1))
    if fixture.captures:
        require(ids[0] != ids[1], "trace_concurrent_identity")
    require(fixture.tracer.current_ids() is None, "trace_concurrent_reset")


async def _exception(fixture: TracerFixture) -> None:
    marker = "fabricated exception content marker"
    error = RuntimeError(marker)
    try:
        with fixture.tracer.start_as_current_span("tether.test.exception"):
            raise error
    except RuntimeError as caught:
        require(caught is error, "trace_primary_exception")
    else:
        require(False, "trace_primary_exception")
    require(fixture.tracer.current_ids() is None, "trace_exception_reset")
    records = await fixture.read()
    if fixture.captures:
        require(len(records) == 6, "trace_capture_count")
        require(all(r.finished for r in records), "trace_closed")
        root = next(r for r in records if r.name == "tether.test.root")
        require(root.parent is None, "trace_root_parent")
        require(root.attributes == {"tether.count": 1, "tether.awaited": True}, "trace_attributes")
        for record in records:
            if record.name in {"tether.test.child", "tether.test.worker"}:
                require(record.parent == root.ids, "trace_recorded_parent")
            if record.name == "tether.test.child":
                require(record.error_reason == "fabricated_failure", "trace_error_reason")
            if record.name == "tether.test.worker":
                require(record.attributes == {"tether.worker": True}, "trace_worker_attributes")
        failed = next(r for r in records if r.name == "tether.test.exception")
        require(failed.error_reason is None and not failed.attributes, "trace_no_exception_capture")
    else:
        require(not records, "trace_noop_capture")
    require(all(marker not in r.model_dump_json() for r in records), "trace_content_absent")


async def tracer_suite(factory: TracerFactory, *, timeout_seconds: float = 10) -> SuiteReport:
    async with asyncio.timeout(timeout(timeout_seconds)), factory() as fixture:
        require(not await fixture.read(), "trace_fresh_fixture")
        await _nest(fixture)
        await _concurrent(fixture)
        await _exception(fixture)
    return SuiteReport(
        suite="tracer",
        capabilities={"captures": fixture.captures},
        checks=(
            "trace_fresh_fixture",
            "trace_initial_context",
            "trace_conditional_ids",
            "trace_root_current",
            "traceparent",
            "trace_child_current",
            "trace_parent_restored",
            "trace_worker_copy",
            "trace_worker_reset",
            "trace_worker_isolation",
            "trace_final_context",
            "trace_concurrent_isolation",
            "trace_concurrent_reset",
            "trace_primary_exception",
            "trace_exception_reset",
            "trace_content_absent",
        )
        + (
            (
                "trace_child_parent",
                "trace_concurrent_identity",
                "trace_capture_count",
                "trace_closed",
                "trace_root_parent",
                "trace_attributes",
                "trace_recorded_parent",
                "trace_error_reason",
                "trace_worker_attributes",
                "trace_no_exception_capture",
            )
            if fixture.captures
            else ("trace_noop_capture",)
        ),
        required_external=(
            "real_trace_context_source",
            "engine_content_registry",
            "tracer_failure_isolation",
            "exporter_receiver_loss_shutdown",
        ),
    )
