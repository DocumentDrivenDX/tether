"""Injected tracing is observational even when its manager/properties fail."""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
from typing import cast

import pytest
from test_telemetry_ports import id_factory

from tether.engine.observers.tracing import GuardedTracer
from tether.testing.telemetry import RecordingTracer
from tether.types.telemetry import TraceIds, Tracer


class BrokenSpan:
    def __init__(self, failure):
        self.failure = failure

    @property
    def ids(self):
        if self.failure == "ids":
            raise RuntimeError("fabricated ids content marker")
        if self.failure == "invalid_ids":
            return {"trace_id": "1" * 32, "span_id": "1" * 16}
        return TraceIds(trace_id="1" * 32, span_id="1" * 16)

    @property
    def traceparent(self):
        if self.failure == "traceparent":
            raise asyncio.CancelledError("fabricated traceparent content marker")
        if self.failure == "mismatch":
            return "fabricated invalid header"
        return TraceIds(trace_id="1" * 32, span_id="1" * 16).traceparent

    def set_attribute(self, name, value):
        if self.failure == "set":
            raise RuntimeError("fabricated set content marker")

    def error(self, reason):
        if self.failure == "error":
            raise asyncio.CancelledError("fabricated reason content marker")


class BrokenManager(AbstractContextManager):
    def __init__(self, failure):
        self.failure = failure
        self.received = []

    def __enter__(self):
        if self.failure == "enter":
            raise RuntimeError("fabricated enter content marker")
        return BrokenSpan(self.failure)

    def __exit__(self, *exception):
        self.received.append(exception)
        if self.failure == "exit":
            raise RuntimeError("fabricated exit content marker")
        return self.failure == "suppress"


class BrokenTracer:
    def __init__(self, failure):
        self.failure = failure
        self.manager = BrokenManager(failure)

    def start_as_current_span(self, name, *, attributes=None):
        if self.failure == "start":
            raise RuntimeError("fabricated start content marker")
        return self.manager

    def current_ids(self):
        raise AssertionError("unsafe ambient context must not be consulted")

    def set_current_attribute(self, name, value):
        raise AssertionError("unsafe ambient mutation must not be used")


@pytest.mark.parametrize(
    "failure",
    [
        "start",
        "enter",
        "ids",
        "invalid_ids",
        "traceparent",
        "mismatch",
        "set",
        "error",
        "exit",
        "suppress",
    ],
)
@pytest.mark.parametrize("cancel", [False, True])
def test_tracer_failures_cannot_replace_or_suppress_primary(failure, cancel):
    raw = BrokenTracer(failure)
    tracer = GuardedTracer(cast(Tracer, raw))
    primary = (
        asyncio.CancelledError("fabricated primary content marker")
        if cancel
        else RuntimeError("fabricated primary content marker")
    )

    def execute():
        with tracer.start_as_current_span("turn") as span:
            span.set_attribute("tether.count", 1)
            span.error("failed")
            raise primary

    with pytest.raises(type(primary)) as caught:
        execute()
    assert caught.value is primary
    assert tracer.current_ids() is None
    if failure not in {"start", "enter"}:
        assert raw.manager.received == [(None, None, None)]
    else:
        assert raw.manager.received == []


@pytest.mark.parametrize("failure", ["ids", "invalid_ids", "traceparent", "mismatch"])
def test_bad_context_never_becomes_outbound_traceparent(failure):
    tracer = GuardedTracer(cast(Tracer, BrokenTracer(failure)))
    with tracer.start_as_current_span("turn") as span:
        assert span.ids is None
        assert span.traceparent is None
        assert tracer.traceparent() is None
    assert tracer.failures >= 1


def test_valid_falsey_tracer_context_is_copied_and_restored():
    class Falsey(RecordingTracer):
        def __bool__(self):
            return False

    raw = Falsey(id_factory(), capacity=2)
    tracer = GuardedTracer(raw)
    with tracer.start_as_current_span("root") as root:
        ids = root.ids
        assert ids is not None
        assert tracer.current_ids() == ids
        assert tracer.traceparent() == ids.traceparent
        tracer.set_current_attribute("tether.count", 2)
        with tracer.start_as_current_span("child") as child:
            assert child.ids != root.ids
        assert tracer.current_ids() == ids
    assert tracer.current_ids() is None
    assert tracer.failures == 0
    assert raw.records[0].attributes == {"tether.count": 2}
    assert all(record.finished for record in raw.records)


def test_noop_trace_never_invents_context_or_effects():
    tracer = GuardedTracer(None)
    tracer.set_current_attribute("unattached", True)
    with tracer.start_as_current_span("root") as span:
        assert span.ids is None
        assert tracer.traceparent() is None
    assert tracer.failures == 0


def test_manager_attribute_lookup_is_guarded():
    class Manager:
        def __getattribute__(self, name):
            raise RuntimeError("fabricated manager attribute content marker")

    raw = BrokenTracer("lookup")
    raw.manager = cast(BrokenManager, Manager())
    tracer = GuardedTracer(cast(Tracer, raw))
    with tracer.start_as_current_span("root") as span:
        assert span.ids is None
    assert tracer.failures >= 1
