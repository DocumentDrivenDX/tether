"""Neutral metadata-only telemetry ports; no tracing SDK or exporter ownership."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Annotated, Protocol, Self

from pydantic import Field, model_validator

from tether.types import JsonValue, Payload

TELEMETRY_CONTRACT_VERSION = 1
TraceScalar = str | int | float | bool


class TraceIds(Payload):
    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    span_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    trace_flags: Annotated[int, Field(ge=0, le=255, strict=True)] = 0

    @model_validator(mode="after")
    def nonzero(self) -> Self:
        if self.trace_id == "0" * 32 or self.span_id == "0" * 16:
            raise ValueError("trace_ids_invalid")
        return self

    @property
    def traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-{self.trace_flags:02x}"


class AuditContext(Payload):
    actor: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    principal: str | None = None
    session_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    spec_id: str | None = None
    spec_version: str | None = None

    @model_validator(mode="after")
    def trace_pair(self) -> Self:
        if self.trace_id is not None or self.span_id is not None:
            if self.trace_id is None or self.span_id is None:
                raise ValueError("trace_ids_invalid")
            TraceIds(trace_id=self.trace_id, span_id=self.span_id)
        return self


class AuditRecord(Payload):
    context: AuditContext
    kind: str
    detail: dict[str, JsonValue]


class AuditSink(Protocol):
    async def emit(
        self, *, context: AuditContext, kind: str, detail: dict[str, JsonValue]
    ) -> None: ...


class Span(Protocol):
    @property
    def ids(self) -> TraceIds | None: ...
    @property
    def traceparent(self) -> str | None: ...
    def set_attribute(self, name: str, value: TraceScalar) -> None: ...
    def error(self, reason: str) -> None: ...


class Tracer(Protocol):
    def start_as_current_span(
        self, name: str, *, attributes: Mapping[str, TraceScalar] | None = None
    ) -> AbstractContextManager[Span]: ...
    def current_ids(self) -> TraceIds | None: ...
    def set_current_attribute(self, name: str, value: TraceScalar) -> None: ...
