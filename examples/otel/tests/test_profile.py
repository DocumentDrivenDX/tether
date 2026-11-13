from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from typing import Any, cast

import httpx
import pytest
from context_lifetime_controls import wait_event_or_task
from fastapi import FastAPI
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from process_lifetime_controls import RetainedCleanup, await_retained
from tether_embed_example.app import FabricatedAuthenticator, client_settings
from tether_otel_example import LocalOtlpProfile, ShutdownReport
from tether_web_api import ApiSettings, create_api

from tether.client import Actor, CallDetail, Change, ClientHost, ClientPorts, EntriesAppended


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
    def __init__(self, loop: asyncio.AbstractEventLoop, entered: asyncio.Event) -> None:
        self.loop = loop
        self.entered = entered
        self.release = Event()
        self.delivered = 0

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.loop.call_soon_threadsafe(self.entered.set)
        self.release.wait()
        self.delivered += len(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self.release.wait()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        del timeout_millis
        return True


def launch(receiver: TraceReceiver, cleanup: RetainedCleanup) -> Thread:
    thread = Thread(target=receiver.serve_forever, daemon=True)

    async def stop() -> bool:
        await asyncio.to_thread(stop_receiver, receiver, thread)
        return True

    cleanup.add(stop)
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
    if thread.ident is not None:
        receiver.shutdown()
    receiver.server_close()
    if thread.ident is not None:
        thread.join()
    assert not thread.is_alive()


async def wait_for_committed_turn(
    stream: AsyncIterator[Change], session_id: str, turn_id: str
) -> None:
    async for event in stream:
        if (
            isinstance(event, EntriesAppended)
            and event.last_in_commit
            and event.session_id == session_id
            and any(
                entry.kind == "turn_ended" and entry.turn_id == turn_id for entry in event.entries
            )
        ):
            return
    raise AssertionError("committed_turn_feed_ended_before_terminal")


async def close_profile(profile: LocalOtlpProfile) -> ShutdownReport:
    await profile.aclose()
    shutdown = profile._shutdown_task
    if shutdown is not None:
        await await_retained(shutdown)
    report = await profile.aclose()
    assert report.drained
    return report


async def settle_cleanup_steps(*steps: Callable[[], Awaitable[Any]]) -> None:
    errors: list[BaseException] = []
    for step in steps:
        try:
            await step()
        except BaseException as error:
            errors.append(error)
    if errors:
        raise BaseExceptionGroup("profile_cleanup_steps_failed", errors)


async def create_attempt(root: Any, profile: LocalOtlpProfile, marker: str) -> Sequence[CallDetail]:
    details: Sequence[CallDetail] | None = None
    async with RetainedCleanup() as cleanup:
        host = await ClientHost.from_ports(
            client_settings(root), ports=ClientPorts(tracer=profile.tracer)
        )
        cleanup.add(host.aclose, host.wait_closed)
        app = FastAPI()
        app.mount(
            "/api",
            create_api(
                clients=host.client,
                authenticator=FabricatedAuthenticator(),
                settings=ApiSettings(api_prefix="/v1"),
            ),
        )
        local_client = host.client(Actor(id="fabricated-local-user"))
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
            baseline = await local_client.snapshot(session_id)
            stream = cast(
                AsyncGenerator[Change, None],
                local_client.follow(session_id=session_id, since=baseline.seq),
            )
            cleanup.add(stream.aclose)
            accepted = await http.post(
                f"/api/v1/sessions/{session_id}/turns",
                json={"request_id": "otel-send", "text": marker},
                headers=headers,
            )
            assert accepted.status_code == 202
            accepted_body = accepted.json()
            await wait_for_committed_turn(stream, session_id, accepted_body["turn_id"])
            completed = await http.get(accepted_body["result_url"], headers=headers)
            assert completed.status_code == 200
            assert completed.json() is not None
        entries = await local_client.entries(session_id)
        call_ids = [
            str(entry.facts["model_call_id"])
            for entry in entries.items
            if entry.kind == "model_call" and isinstance(entry.facts.get("model_call_id"), str)
        ]
        details = await local_client.call_detail(session_id, call_ids)
    assert details is not None
    return details


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
        async with RetainedCleanup() as cleanup:
            receiver = TraceReceiver()
            launch(receiver, cleanup)
            profile = LocalOtlpProfile(endpoint=receiver.endpoint)

            async def drain_profile() -> bool:
                await close_profile(profile)
                return True

            cleanup.add(drain_profile)
            marker = "_".join(("PRIVATE", "PROMPT", "AND", "CREDENTIAL", "MARKER"))
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
            report = await close_profile(profile)
            assert report.drained
            assert report.stats.delivered > 0

    asyncio.run(exercise())


async def assert_queue_loss(cleanup: RetainedCleanup) -> None:
    entered = asyncio.Event()
    exporter = BlockingExporter(asyncio.get_running_loop(), entered)
    profile = LocalOtlpProfile(
        endpoint="http://127.0.0.1:4318/v1/traces",
        queue_capacity=1,
        batch_size=1,
        schedule_delay_seconds=0.01,
        export_timeout_seconds=2,
        shutdown_timeout_seconds=0.02,
        exporter=exporter,
    )
    worker = profile._processor._processor._batch_processor._worker_thread
    worker_join = asyncio.create_task(asyncio.to_thread(worker.join))
    shutdown: asyncio.Task | None = None

    async def drain_blocked_profile() -> bool:
        async def settle_shutdown() -> None:
            if shutdown is not None:
                await await_retained(shutdown)

        await settle_cleanup_steps(
            lambda: close_profile(profile),
            settle_shutdown,
            lambda: await_retained(worker_join),
        )
        return True

    cleanup.add(drain_blocked_profile)

    async def release_exporter() -> bool:
        exporter.release.set()
        return True

    cleanup.add(release_exporter)
    with profile.tracer.start_as_current_span("tether.queue.first"):
        pass
    await wait_event_or_task(entered, worker_join)
    for index in range(20):
        with profile.tracer.start_as_current_span(f"tether.queue.item_{index}"):
            pass
    shutdown = asyncio.create_task(profile.aclose())
    outstanding = await shutdown
    assert outstanding.outstanding
    assert outstanding.stats.sampled == 21
    exporter.release.set()
    assert profile._shutdown_task is not None
    await await_retained(profile._shutdown_task)
    await await_retained(worker_join)
    drained = await profile.aclose()
    assert drained.drained
    assert drained.stats.pending == 0
    assert drained.stats.dropped > 0
    assert (
        drained.stats.sampled
        == drained.stats.delivered + drained.stats.failed + drained.stats.dropped
    )


def test_sampling_and_bounded_queue_loss_are_reported_with_retained_shutdown_owner() -> None:
    async def exercise() -> None:
        async with RetainedCleanup() as cleanup:
            sampler = LocalOtlpProfile(endpoint="http://127.0.0.1:4318/v1/traces", sample_ratio=0)

            async def drain_sampler() -> bool:
                await close_profile(sampler)
                return True

            cleanup.add(drain_sampler)
            with sampler.tracer.start_as_current_span("tether.sampled_out"):
                pass
            sampled_report = await close_profile(sampler)
            assert sampled_report.drained
            assert sampled_report.stats.started == 1
            assert sampled_report.stats.sampled == 0
            await assert_queue_loss(cleanup)

    asyncio.run(exercise())


def test_receiver_failure_is_counted_without_exception_details(tmp_path) -> None:
    async def exercise() -> None:
        async with RetainedCleanup() as cleanup:
            receiver = TraceReceiver(status=503)
            launch(receiver, cleanup)
            profile = LocalOtlpProfile(endpoint=receiver.endpoint, export_timeout_seconds=0.5)

            async def drain_profile() -> bool:
                await close_profile(profile)
                return True

            cleanup.add(drain_profile)
            with pytest.raises(RuntimeError, match="PRIVATE_FAILURE_MARKER"):
                raise_failed_span(profile)
            report = await close_profile(profile)
            assert report.drained
            assert report.stats.failed > 0
            assert b"PRIVATE_FAILURE_MARKER" not in b"".join(receiver.bodies)

    asyncio.run(exercise())
