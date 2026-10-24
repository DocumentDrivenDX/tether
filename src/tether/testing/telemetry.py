"""Bounded recording doubles; explicit fabricated ids do not prove real ingestion."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock

from tether.ports.telemetry import NullSpan
from tether.types import JsonValue, Payload
from tether.types.canonical import canonical
from tether.types.telemetry import AuditContext, AuditRecord, Span, TraceIds, TraceScalar


def positive(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("telemetry_capacity_invalid")
    return value


def trace_scalar(value: object) -> TraceScalar:
    if not isinstance(value, (str, int, float, bool)):
        raise ValueError("trace_attribute_invalid")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("trace_attribute_invalid")
    return value


def attribute_size(name: object, value: TraceScalar) -> int:
    if not isinstance(name, str):
        raise ValueError("trace_attribute_invalid")
    try:
        return max(len(name.encode()), len(canonical(value)))
    except (ValueError, TypeError):
        raise ValueError("trace_attribute_invalid") from None


def span_name_size(name: object) -> int:
    if not isinstance(name, str):
        raise ValueError("trace_name_invalid")
    try:
        return len(name.encode())
    except UnicodeError:
        raise ValueError("trace_name_invalid") from None


def reason_valid(reason: object) -> bool:
    return isinstance(reason, str) and re.fullmatch(r"[a-z][a-z0-9_.]{0,95}", reason) is not None


class ListAuditSink:
    def __init__(self, *, capacity: int, max_record_bytes: int = 16384) -> None:
        self.capacity, self.max_record_bytes = positive(capacity), positive(max_record_bytes)
        self.overflow = self.oversized = 0
        self._records: list[AuditRecord] = []
        self._lock = Lock()

    @property
    def records(self) -> tuple[AuditRecord, ...]:
        with self._lock:
            return tuple(record.model_copy(deep=True) for record in self._records)

    async def emit(self, *, context: AuditContext, kind: str, detail: dict[str, JsonValue]) -> None:
        with self._lock:
            if len(self._records) >= self.capacity:
                self.overflow += 1
                return
        record = AuditRecord(context=context, kind=kind, detail=detail)
        if len(canonical(record.model_dump(mode="json"))) > self.max_record_bytes:
            with self._lock:
                self.oversized += 1
            return
        with self._lock:
            if len(self._records) >= self.capacity:
                self.overflow += 1
                return
            self._records.append(record.model_copy(deep=True))


class TraceObservation(Payload):
    name: str
    ids: TraceIds
    parent: TraceIds | None
    attributes: dict[str, TraceScalar]
    error_reason: str | None
    finished: bool
    overflow: int


class RecordedSpan:
    def __init__(
        self,
        name: str,
        ids: TraceIds,
        parent: TraceIds | None,
        max_attributes: int,
        max_value_bytes: int,
    ) -> None:
        self.name, self.ids, self.parent = name, ids, parent
        self.max_attributes, self.max_value_bytes = max_attributes, max_value_bytes
        self._attributes: dict[str, TraceScalar] = {}
        self._error: str | None = None
        self._finished = False
        self._overflow = 0
        self._lock = Lock()

    @property
    def traceparent(self) -> str:
        return self.ids.traceparent

    def set_attribute(self, name: str, value: TraceScalar) -> None:
        value = trace_scalar(value)
        oversized = attribute_size(name, value) > self.max_value_bytes
        with self._lock:
            if self._finished:
                raise RuntimeError("span_finished")
            if oversized or (
                name not in self._attributes and len(self._attributes) >= self.max_attributes
            ):
                self._overflow += 1
                return
            self._attributes[name] = value

    def error(self, reason: str) -> None:
        if not reason_valid(reason):
            raise ValueError("trace_reason_invalid")
        with self._lock:
            if self._finished:
                raise RuntimeError("span_finished")
            self._error = reason

    def finish(self) -> None:
        with self._lock:
            self._finished = True

    def snapshot(self) -> TraceObservation:
        with self._lock:
            return TraceObservation(
                name=self.name,
                ids=self.ids,
                parent=self.parent,
                attributes=dict(self._attributes),
                error_reason=self._error,
                finished=self._finished,
                overflow=self._overflow,
            )


class RecordingTracer:
    def __init__(
        self,
        id_factory: Callable[[TraceIds | None], TraceIds],
        *,
        capacity: int,
        max_attributes: int = 32,
        max_value_bytes: int = 1024,
    ) -> None:
        self.id_factory = id_factory
        self.capacity, self.max_attributes = positive(capacity), positive(max_attributes)
        self.max_value_bytes = positive(max_value_bytes)
        self.overflow = self._reserved = 0
        self._records: list[RecordedSpan] = []
        self._lock = Lock()
        self._current: ContextVar[RecordedSpan | None] = ContextVar(
            "tether_test_span", default=None
        )

    @property
    def records(self) -> tuple[TraceObservation, ...]:
        with self._lock:
            return tuple(record.snapshot() for record in self._records)

    def _reserve(self) -> bool:
        with self._lock:
            if len(self._records) + self._reserved >= self.capacity:
                self.overflow += 1
                return False
            self._reserved += 1
            return True

    def _create(self, name: str, attributes: Mapping[str, TraceScalar] | None) -> RecordedSpan:
        try:
            if span_name_size(name) > self.max_value_bytes:
                raise ValueError("trace_name_invalid")
            parent = self.current_ids()
            ids = TraceIds.model_validate(self.id_factory(parent)).model_copy(deep=True)
            if parent is not None and (
                ids.trace_id != parent.trace_id or ids.span_id == parent.span_id
            ):
                raise ValueError("trace_parent_invalid")
            span = RecordedSpan(name, ids, parent, self.max_attributes, self.max_value_bytes)
            if attributes is not None:
                for key, value in attributes.items():
                    span.set_attribute(key, value)
        except BaseException:
            with self._lock:
                self._reserved -= 1
            raise
        with self._lock:
            self._records.append(span)
            self._reserved -= 1
        return span

    @contextmanager
    def start_as_current_span(
        self, name: str, *, attributes: Mapping[str, TraceScalar] | None = None
    ) -> Generator[Span, None, None]:
        if not self._reserve():
            yield NullSpan()
            return
        span = self._create(name, attributes)
        token = self._current.set(span)
        try:
            yield span
        finally:
            span.finish()
            self._current.reset(token)

    def current_ids(self) -> TraceIds | None:
        current = self._current.get()
        return current.ids if current is not None else None

    def set_current_attribute(self, name: str, value: TraceScalar) -> None:
        current = self._current.get()
        if current is not None:
            current.set_attribute(name, value)
