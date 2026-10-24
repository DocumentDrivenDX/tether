"""Secondary tracing failures cannot capture, suppress or replace execution errors."""

from __future__ import annotations

from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from typing import TypeVar

from tether.ports.telemetry import NullSpan, NullTracer
from tether.types.telemetry import Span, TraceIds, Tracer, TraceScalar

T = TypeVar("T")


class GuardedSpan:
    def __init__(self, tracer: GuardedTracer, span: Span) -> None:
        self._tracer, self._span = tracer, span
        self._ids = tracer.guard(lambda: copy_ids(span.ids), None)
        parent = tracer.guard(lambda: span.traceparent, None)
        if parent != (self._ids.traceparent if self._ids is not None else None):
            tracer.failed()
            self._ids = None

    @property
    def ids(self) -> TraceIds | None:
        return self._ids

    @property
    def traceparent(self) -> str | None:
        return self._ids.traceparent if self._ids is not None else None

    def set_attribute(self, name: str, value: TraceScalar) -> None:
        self._tracer.guard(lambda: self._span.set_attribute(name, value), None)

    def error(self, reason: str) -> None:
        self._tracer.guard(lambda: self._span.error(reason), None)


def copy_ids(value: object) -> TraceIds | None:
    if value is None:
        return None
    if not isinstance(value, TraceIds):
        raise ValueError("trace_ids_invalid")
    return TraceIds.model_validate(value.model_dump(mode="python"))


class GuardedTracer:
    def __init__(self, tracer: Tracer | None) -> None:
        self.tracer = tracer if tracer is not None else NullTracer()
        self._failures = 0
        self._lock = Lock()
        self._current: ContextVar[GuardedSpan | None] = ContextVar(
            "tether_guarded_span", default=None
        )

    @property
    def failures(self) -> int:
        with self._lock:
            return self._failures

    def failed(self) -> None:
        with self._lock:
            self._failures += 1

    def guard(self, function: Callable[[], T], fallback: T) -> T:
        try:
            return function()
        except BaseException:
            # This channel cannot become execution control, including during primary cancellation.
            self.failed()
            return fallback

    @contextmanager
    def start_as_current_span(
        self, name: str, *, attributes: Mapping[str, TraceScalar] | None = None
    ) -> Generator[Span, None, None]:
        manager = self.guard(
            lambda: self.tracer.start_as_current_span(name, attributes=attributes), None
        )
        entered, raw = self.guard(
            lambda: (True, manager.__enter__()) if manager is not None else (False, None),
            (False, None),
        )
        span = GuardedSpan(self, raw if raw is not None else NullSpan())
        token = self._current.set(span)
        try:
            yield span
        finally:
            self._current.reset(token)
            if entered and manager is not None:
                # Never give an observer the body's exception object or a suppression vote.
                self.guard(lambda: manager.__exit__(None, None, None), None)

    def current_ids(self) -> TraceIds | None:
        span = self._current.get()
        return span.ids if span is not None else None

    def traceparent(self) -> str | None:
        span = self._current.get()
        return span.traceparent if span is not None else None

    def set_current_attribute(self, name: str, value: TraceScalar) -> None:
        span = self._current.get()
        if span is not None:
            span.set_attribute(name, value)
