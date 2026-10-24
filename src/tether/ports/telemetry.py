"""Default no-op telemetry: no I/O, retained records, ambient SDKs or invented ids."""

from collections.abc import Generator, Mapping
from contextlib import contextmanager

from tether.types import JsonValue
from tether.types.telemetry import AuditContext, Span, TraceIds, TraceScalar


class NullSpan:
    @property
    def ids(self) -> TraceIds | None:
        return None

    @property
    def traceparent(self) -> str | None:
        return None

    def set_attribute(self, name: str, value: TraceScalar) -> None:
        pass

    def error(self, reason: str) -> None:
        pass


class NullTracer:
    @contextmanager
    def start_as_current_span(
        self, name: str, *, attributes: Mapping[str, TraceScalar] | None = None
    ) -> Generator[Span, None, None]:
        yield NullSpan()

    def current_ids(self) -> TraceIds | None:
        return None

    def set_current_attribute(self, name: str, value: TraceScalar) -> None:
        pass


class NoopAuditSink:
    async def emit(self, *, context: AuditContext, kind: str, detail: dict[str, JsonValue]) -> None:
        pass
