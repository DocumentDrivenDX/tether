"""Explicit, bounded OTLP/HTTP tracing adapter for the public Tether port."""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from threading import Lock
from types import TracebackType
from urllib.parse import urlsplit

import httpx
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Span as OtelSpan
from opentelemetry.trace import Status, StatusCode

from tether.client import Span, TraceIds, Tracer, TraceScalar

_SAFE_ATTRIBUTES = frozenset(
    {
        "tether.action",
        "tether.origin",
        "tether.allowed",
        "tether.reason",
        "tether.purpose",
        "tether.usage_complete",
        "tether.input_tokens",
        "tether.output_tokens",
        "tether.cache_read_tokens",
        "tether.cache_write_tokens",
        "tether.reasoning_tokens",
    }
)
_SAFE_TOKEN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SAFE_SERVICE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")


@dataclass(frozen=True)
class TelemetryStats:
    started: int
    sampled: int
    delivered: int
    failed: int
    pending: int
    dropped: int
    queue_capacity: int


@dataclass(frozen=True)
class ShutdownReport:
    drained: bool
    outstanding: bool
    stats: TelemetryStats


class _CountingExporter(SpanExporter):
    def __init__(self, delegate: SpanExporter) -> None:
        self._delegate = delegate
        self._lock = Lock()
        self.delivered = 0
        self.failed = 0

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            result = self._delegate.export(spans)
        except Exception:
            result = SpanExportResult.FAILURE
        with self._lock:
            if result is SpanExportResult.SUCCESS:
                self.delivered += len(spans)
            else:
                self.failed += len(spans)
        return result

    def shutdown(self) -> None:
        self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        flush = getattr(self._delegate, "force_flush", None)
        return True if flush is None else bool(flush(timeout_millis))


class _LoopbackOtlpExporter(SpanExporter):
    """Exports protobuf directly without ambient headers, proxies, or credentials."""

    def __init__(self, endpoint: str, timeout: float) -> None:
        self._endpoint = endpoint
        self._client = httpx.Client(trust_env=False, timeout=timeout)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        body = encode_spans(spans).SerializeToString()
        try:
            response = self._client.post(
                self._endpoint,
                content=body,
                headers={"Content-Type": "application/x-protobuf"},
            )
            return (
                SpanExportResult.SUCCESS
                if response.status_code == 200
                else SpanExportResult.FAILURE
            )
        except (httpx.HTTPError, OSError):
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        self._client.close()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        del timeout_millis
        return True


class _CountingProcessor(SpanProcessor):
    def __init__(
        self,
        exporter: _CountingExporter,
        *,
        capacity: int,
        batch_size: int,
        delay_ms: int,
        timeout_ms: int,
    ) -> None:
        self._exporter = exporter
        self._capacity = capacity
        self._processor = BatchSpanProcessor(
            exporter,
            max_queue_size=capacity,
            max_export_batch_size=batch_size,
            schedule_delay_millis=delay_ms,
            export_timeout_millis=timeout_ms,
        )
        self._lock = Lock()
        self.started = 0
        self.sampled = 0
        self.closed = False

    def note_started(self) -> None:
        with self._lock:
            self.started += 1

    def on_start(self, span: OtelSpan, parent_context: object = None) -> None:
        del span, parent_context

    def on_end(self, span: ReadableSpan) -> None:
        with self._lock:
            self.sampled += 1
        self._processor.on_end(span)

    def shutdown(self) -> None:
        self._processor.shutdown()
        with self._lock:
            self.closed = True

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._processor.force_flush(timeout_millis)

    def stats(self) -> TelemetryStats:
        with self._lock:
            started, sampled, closed = self.started, self.sampled, self.closed
        with self._exporter._lock:
            delivered, failed = self._exporter.delivered, self._exporter.failed
        remainder = max(0, sampled - delivered - failed)
        return TelemetryStats(
            started=started,
            sampled=sampled,
            delivered=delivered,
            failed=failed,
            pending=0 if closed else remainder,
            dropped=remainder if closed else 0,
            queue_capacity=self._capacity,
        )


class _OtelSpan:
    def __init__(self, span: OtelSpan) -> None:
        self._span = span
        self._ids = _span_ids(span)

    @property
    def ids(self) -> TraceIds | None:
        return self._ids

    @property
    def traceparent(self) -> str | None:
        return self._ids.traceparent if self._ids is not None else None

    def set_attribute(self, name: str, value: TraceScalar) -> None:
        safe = _safe_attribute(name, value)
        if safe is not None:
            self._span.set_attribute(name, safe)

    def error(self, reason: str) -> None:
        code = reason if _SAFE_TOKEN.fullmatch(reason) else "failed"
        self._span.set_status(Status(StatusCode.ERROR, description=code))


class _OtelTracer(Tracer):
    def __init__(self, provider: TracerProvider, processor: _CountingProcessor) -> None:
        self._tracer = provider.get_tracer("tether-embed-example", "0.2.0")
        self._processor = processor

    def start_as_current_span(
        self, name: str, *, attributes: Mapping[str, TraceScalar] | None = None
    ) -> AbstractContextManager[Span]:
        safe_name = name if re.fullmatch(r"tether\.[a-z0-9_.]+", name) else "tether.operation"
        safe_attributes = _safe_attributes(attributes)
        self._processor.note_started()
        return _SpanContext(
            self._tracer.start_as_current_span(
                safe_name,
                attributes=safe_attributes,
                record_exception=False,
                set_status_on_exception=False,
            )
        )

    def current_ids(self) -> TraceIds | None:
        return _span_ids(trace.get_current_span())

    def set_current_attribute(self, name: str, value: TraceScalar) -> None:
        span = trace.get_current_span()
        if span.is_recording():
            safe = _safe_attribute(name, value)
            if safe is not None:
                span.set_attribute(name, safe)


class _SpanContext:
    def __init__(self, manager: AbstractContextManager[OtelSpan]) -> None:
        self._manager = manager

    def __enter__(self) -> Span:
        span = self._manager.__enter__()
        return _OtelSpan(span)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        return bool(self._manager.__exit__(exc_type, exc, traceback))


class LocalOtlpProfile:
    """Owns one non-global provider and keeps it alive through bounded shutdown."""

    def __init__(
        self,
        *,
        endpoint: str,
        service_name: str = "tether-embed-example",
        sample_ratio: float = 1.0,
        queue_capacity: int = 256,
        batch_size: int = 32,
        schedule_delay_seconds: float = 0.1,
        export_timeout_seconds: float = 1.0,
        shutdown_timeout_seconds: float = 2.0,
        exporter: SpanExporter | None = None,
    ) -> None:
        _validate_settings(
            endpoint,
            service_name,
            sample_ratio,
            queue_capacity,
            batch_size,
            schedule_delay_seconds,
            export_timeout_seconds,
            shutdown_timeout_seconds,
        )
        selected = (
            exporter
            if exporter is not None
            else _LoopbackOtlpExporter(endpoint, export_timeout_seconds)
        )
        self._exporter = _CountingExporter(selected)
        self._processor = _CountingProcessor(
            self._exporter,
            capacity=queue_capacity,
            batch_size=batch_size,
            delay_ms=int(schedule_delay_seconds * 1000),
            timeout_ms=int(export_timeout_seconds * 1000),
        )
        self._provider = TracerProvider(
            resource=Resource({"service.name": service_name}),
            sampler=ParentBased(root=TraceIdRatioBased(sample_ratio)),
            shutdown_on_exit=False,
        )
        self._provider.add_span_processor(self._processor)
        self.tracer: Tracer = _OtelTracer(self._provider, self._processor)
        self._shutdown_timeout = shutdown_timeout_seconds
        self._shutdown_task: asyncio.Task[None] | None = None

    @property
    def stats(self) -> TelemetryStats:
        return self._processor.stats()

    async def flush(self, timeout_seconds: float = 2.0) -> bool:
        timeout = _finite_positive(timeout_seconds, "flush_timeout_invalid")
        result = await asyncio.to_thread(self._provider.force_flush, int(timeout * 1000))
        return bool(result)

    async def aclose(self) -> ShutdownReport:
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(asyncio.to_thread(self._provider.shutdown))
        done, _pending = await asyncio.wait({self._shutdown_task}, timeout=self._shutdown_timeout)
        if not done:
            return ShutdownReport(drained=False, outstanding=True, stats=self.stats)
        await self._shutdown_task
        return ShutdownReport(drained=True, outstanding=False, stats=self.stats)


def create_profile(
    endpoint: str,
    *,
    service_name: str = "tether-embed-example",
    sample_ratio: float = 1.0,
    queue_capacity: int = 256,
    batch_size: int = 32,
    schedule_delay_seconds: float = 0.1,
    export_timeout_seconds: float = 1.0,
    shutdown_timeout_seconds: float = 2.0,
) -> LocalOtlpProfile:
    return LocalOtlpProfile(
        endpoint=endpoint,
        service_name=service_name,
        sample_ratio=sample_ratio,
        queue_capacity=queue_capacity,
        batch_size=batch_size,
        schedule_delay_seconds=schedule_delay_seconds,
        export_timeout_seconds=export_timeout_seconds,
        shutdown_timeout_seconds=shutdown_timeout_seconds,
    )


def _validate_settings(
    endpoint: str,
    service_name: str,
    sample_ratio: float,
    queue_capacity: int,
    batch_size: int,
    delay: float,
    export_timeout: float,
    shutdown_timeout: float,
) -> None:
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.port is None
        or parsed.path not in {"/v1/traces", "/"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("local_otlp_endpoint_invalid")
    if not _SAFE_SERVICE.fullmatch(service_name):
        raise ValueError("service_name_invalid")
    if not math.isfinite(sample_ratio) or not 0 <= sample_ratio <= 1:
        raise ValueError("sample_ratio_invalid")
    if queue_capacity < 1 or batch_size < 1 or batch_size > queue_capacity:
        raise ValueError("queue_limits_invalid")
    for value, code in (
        (delay, "schedule_delay_invalid"),
        (export_timeout, "export_timeout_invalid"),
        (shutdown_timeout, "shutdown_timeout_invalid"),
    ):
        _finite_positive(value, code)


def _finite_positive(value: float, code: str) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(code)
    return value


def _safe_attributes(values: Mapping[str, TraceScalar] | None) -> dict[str, TraceScalar]:
    return {
        key: safe
        for key, value in (values or {}).items()
        if (safe := _safe_attribute(key, value)) is not None
    }


def _safe_attribute(name: str, value: TraceScalar) -> TraceScalar | None:
    if name not in _SAFE_ATTRIBUTES:
        return None
    if isinstance(value, str):
        return value if _SAFE_TOKEN.fullmatch(value) else None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (int, bool)):
        return value
    return None


def _span_ids(span: OtelSpan) -> TraceIds | None:
    context = span.get_span_context()
    if not context.is_valid:
        return None
    return TraceIds(
        trace_id=f"{context.trace_id:032x}",
        span_id=f"{context.span_id:016x}",
        trace_flags=int(context.trace_flags),
    )
