from __future__ import annotations

import asyncio
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from tether_embed_example.app import FabricatedAuthenticator, client_settings
from tether_otel_example import LocalOtlpProfile
from tether_web_api import ApiSettings, create_api

from tether.client import Actor, CallDetail, ClientHost, ClientPorts


class TraceReceiver(ThreadingHTTPServer):
    def __init__(self, *, status: int = 200) -> None:
        self.status = status
        self.bodies: list[bytes] = []
        self.request_headers: list[dict[str, str]] = []
        super().__init__(("127.0.0.1", 0), TraceHandler)

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.server_port}/v1/traces"


class TraceHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        receiver = self.server
        if isinstance(receiver, TraceReceiver):
            receiver.bodies.append(body)
            receiver.request_headers.append(dict(self.headers.items()))
            self.send_response(receiver.status)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        del format
        del args


class BlockingExporter(SpanExporter):
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.delivered = 0

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.started.set()
        self.release.wait(5)
        self.delivered += len(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self.release.set()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        del timeout_millis
        return True


def launch(receiver: TraceReceiver) -> Thread:
    thread = Thread(target=receiver.serve_forever, daemon=True)
    thread.start()
    return thread


def received_spans(bodies: list[bytes]) -> list[Any]:
    result: list[Any] = []
    for body in bodies:
        request = ExportTraceServiceRequest.FromString(body)
        result.extend(
            span
            for resource in request.resource_spans
            for scope in resource.scope_spans
            for span in scope.spans
        )
    return result


def stop_receiver(receiver: TraceReceiver, thread: Thread) -> None:
    receiver.shutdown()
    receiver.server_close()
    thread.join(timeout=2)


async def wait_for_result(
    http: httpx.AsyncClient, result_url: str, headers: dict[str, str]
) -> None:
    for _attempt in range(100):
        response = await http.get(result_url, headers=headers)
        assert response.status_code == 200
        if response.json() is not None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("turn_result_timeout")


async def create_attempt(root: Any, profile: LocalOtlpProfile, marker: str) -> Sequence[CallDetail]:
    host = await ClientHost.from_ports(
        client_settings(root), ports=ClientPorts(tracer=profile.tracer)
    )
    app = FastAPI()
    app.mount(
        "/api",
        create_api(
            clients=host.client,
            authenticator=FabricatedAuthenticator(),
            settings=ApiSettings(api_prefix="/v1"),
        ),
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://embed.test"
        ) as http:
            headers = {"Authorization": f"Bearer {marker}"}
            created = await http.post(
                "/api/v1/sessions",
                json={"request_id": "otel-create", "spec": "demo"},
                headers=headers,
            )
            assert created.status_code == 201
            session_id = created.json()["id"]
            accepted = await http.post(
                f"/api/v1/sessions/{session_id}/turns",
                json={"request_id": "otel-send", "text": marker},
                headers=headers,
            )
            assert accepted.status_code == 202
            await wait_for_result(http, accepted.json()["result_url"], headers)
        client = host.client(Actor(id="fabricated-local-user"))
        entries = await client.entries(session_id)
        call_ids = [
            str(entry.facts["model_call_id"])
            for entry in entries.items
            if entry.kind == "model_call" and isinstance(entry.facts.get("model_call_id"), str)
        ]
        return await client.call_detail(session_id, call_ids)
    finally:
        assert await host.aclose()


def raise_exception_probe(profile: LocalOtlpProfile) -> None:
    with profile.tracer.start_as_current_span("tether.exception_probe"):
        raise RuntimeError("PRIVATE_EXCEPTION_MARKER")


def raise_failed_span(profile: LocalOtlpProfile) -> None:
    with profile.tracer.start_as_current_span("tether.failure") as span:
        span.error("failed")
        raise RuntimeError("PRIVATE_FAILURE_MARKER")


def test_real_otlp_http_spans_join_public_api_attempts_and_exclude_content(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "authorization=AMBIENT_CREDENTIAL_MARKER")
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "deployment.environment=AMBIENT_RESOURCE_MARKER")

    async def exercise() -> None:
        receiver = TraceReceiver()
        thread = launch(receiver)
        profile = LocalOtlpProfile(endpoint=receiver.endpoint)
        marker = "_".join(("PRIVATE", "PROMPT", "AND", "CREDENTIAL", "MARKER"))
        try:
            details = await create_attempt(tmp_path / "journal", profile, marker)
            assert details
            with pytest.raises(RuntimeError, match="PRIVATE_EXCEPTION_MARKER"):
                raise_exception_probe(profile)
            assert await profile.flush()
            spans = received_spans(receiver.bodies)
            joined = [(detail.identity.trace_id, detail.identity.span_id) for detail in details]
            assert any((span.trace_id.hex(), span.span_id.hex()) in joined for span in spans)
            assert marker.encode() not in b"".join(receiver.bodies)
            assert b"PRIVATE_EXCEPTION_MARKER" not in b"".join(receiver.bodies)
            assert b"AMBIENT_RESOURCE_MARKER" not in b"".join(receiver.bodies)
            assert all(
                "AMBIENT_CREDENTIAL_MARKER" not in str(headers)
                for headers in receiver.request_headers
            )
            assert all(not span.events for span in spans)
        finally:
            report = await profile.aclose()
            stop_receiver(receiver, thread)
            assert report.drained
            assert report.stats.delivered > 0

    asyncio.run(exercise())


def test_sampling_and_bounded_queue_loss_are_reported_with_retained_shutdown_owner() -> None:
    async def exercise() -> None:
        sampler = LocalOtlpProfile(endpoint="http://127.0.0.1:4318/v1/traces", sample_ratio=0)
        with sampler.tracer.start_as_current_span("tether.sampled_out"):
            pass
        sampled_report = await sampler.aclose()
        assert sampled_report.drained
        assert sampled_report.stats.started == 1
        assert sampled_report.stats.sampled == 0

        exporter = BlockingExporter()
        profile = LocalOtlpProfile(
            endpoint="http://127.0.0.1:4318/v1/traces",
            queue_capacity=1,
            batch_size=1,
            schedule_delay_seconds=0.01,
            export_timeout_seconds=2,
            shutdown_timeout_seconds=0.02,
            exporter=exporter,
        )
        with profile.tracer.start_as_current_span("tether.queue.first"):
            pass
        assert await asyncio.to_thread(exporter.started.wait, 2)
        for index in range(20):
            with profile.tracer.start_as_current_span(f"tether.queue.item_{index}"):
                pass
        outstanding = await profile.aclose()
        assert outstanding.outstanding
        assert outstanding.stats.sampled == 21
        exporter.release.set()
        drained = await profile.aclose()
        assert drained.drained
        assert drained.stats.pending == 0
        assert drained.stats.dropped > 0
        assert drained.stats.sampled == (
            drained.stats.delivered + drained.stats.failed + drained.stats.dropped
        )

    asyncio.run(exercise())


def test_receiver_failure_is_counted_without_exception_details(tmp_path) -> None:
    async def exercise() -> None:
        receiver = TraceReceiver(status=503)
        thread = launch(receiver)
        profile = LocalOtlpProfile(endpoint=receiver.endpoint, export_timeout_seconds=0.5)
        try:
            with pytest.raises(RuntimeError, match="PRIVATE_FAILURE_MARKER"):
                raise_failed_span(profile)
            report = await profile.aclose()
            assert report.drained
            assert report.stats.failed > 0
            assert b"PRIVATE_FAILURE_MARKER" not in b"".join(receiver.bodies)
        finally:
            await profile.aclose()
            stop_receiver(receiver, thread)

    asyncio.run(exercise())
