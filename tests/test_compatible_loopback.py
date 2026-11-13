"""The selected compatible adapter crosses a real local HTTP socket once per attempt."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import cast

import loopback_lifetime_controls
import pytest
from loopback_lifetime_controls import (
    DeferredRequestTimeout,
    HeldCleanupLoopback,
    OwnedLoopback,
)

from tether.adapters.openai_compat import OpenAICompatModel
from tether.adapters.pydantic_ai import compatible as compatible_module
from tether.adapters.pydantic_ai.wire import StreamingWireValidator
from tether.models import complete
from tether.models.testing import ListAttemptRecorder
from tether.types import (
    ModelExecutionLimits,
    ModelRequest,
    ModelResponse,
    ModelUnavailable,
    TextDelta,
    ToolSpec,
)


class FreshCredential:
    def __init__(self) -> None:
        self.reads = 0

    async def authorization(self) -> str:
        self.reads += 1
        return " ".join(("Synthetic", "actor", str(self.reads)))


def wire_response() -> bytes:
    return json.dumps(
        {
            "id": "fabricated-body-id",
            "object": "chat.completion",
            "created": 1,
            "model": "fabricated-wire-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "Fabricated answer.",
                        "tool_calls": None,
                    },
                }
            ],
        }
    ).encode()


async def read_request(reader: asyncio.StreamReader) -> tuple[str, dict[str, str], dict]:
    head = await reader.readuntil(b"\r\n\r\n")
    lines = head.decode("ascii").split("\r\n")
    headers = {}
    for line in lines[1:]:
        if line:
            name, value = line.split(": ", 1)
            headers[name.lower()] = value
    body = await reader.readexactly(int(headers["content-length"]))
    return lines[0], headers, json.loads(body)


class LoopbackPeer:
    def __init__(self) -> None:
        self.seen: list[tuple[str, dict[str, str], dict]] = []

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            self.seen.append(await read_request(reader))
            first = len(self.seen) == 1
            payload = b'{"error":"fabricated outage"}' if first else wire_response()
            status = b"503 Service Unavailable" if first else b"200 OK"
            request_id = b"fabricated-failed" if first else b"fabricated-success"
            writer.write(
                b"HTTP/1.1 "
                + status
                + b"\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\nConnection: close\r\nRetry-After: 0\r\nX-Request-Id: "
                + request_id
                + b"\r\n\r\n"
                + payload
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


class RetryAfterPeer:
    def __init__(self) -> None:
        self.requests = 0

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await read_request(reader)
            self.requests += 1
            payload = b'{"error":"fabricated outage"}'
            writer.write(
                b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\n"
                b"Retry-After: 30\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + payload
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


def stream_event(delta: dict, finish: str | None = None, *, created: int = 1) -> bytes:
    frame = {
        "id": "fabricated-stream-id",
        "object": "chat.completion.chunk",
        "created": created,
        "model": "fabricated-wire-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return b"data: " + json.dumps(frame).encode() + b"\n\n"


def test_stream_timestamp_may_advance_but_cannot_move_backward(
    limits: ModelExecutionLimits,
) -> None:
    accepted = StreamingWireValidator((), limits)
    accepted.feed(stream_event({"role": "assistant", "content": "A"}, created=1))
    accepted.feed(stream_event({"content": "B"}, "stop", created=2))
    accepted.feed(b"data: [DONE]\n\n")
    assert accepted.finish().content == "AB"

    reversed_time = StreamingWireValidator((), limits)
    reversed_time.feed(stream_event({"role": "assistant", "content": "A"}, created=2))
    with pytest.raises(ModelUnavailable, match="invalid_response"):
        reversed_time.feed(stream_event({"content": "B"}, "stop", created=1))


class StreamingPeer:
    def __init__(self) -> None:
        self.first_sent = asyncio.Event()
        self.release = asyncio.Event()
        self.request: tuple[str, dict[str, str], dict] | None = None

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            self.request = await read_request(reader)
            first = stream_event({"role": "assistant", "content": "Fabricated "})
            rest = (
                stream_event({"content": "answer."})
                + stream_event({}, "stop")
                + b"data: [DONE]\n\n"
            )
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: "
                + str(len(first) + len(rest)).encode()
                + b"\r\nConnection: close\r\nX-Request-Id: fabricated-stream-header\r\n\r\n"
                + first
            )
            await writer.drain()
            self.first_sent.set()
            await self.release.wait()
            writer.write(rest)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


class IncompleteStreamPeer:
    def __init__(self, *, truncated_body: bool) -> None:
        self.truncated_body = truncated_body
        self.requests = 0

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await read_request(reader)
            self.requests += 1
            first = stream_event({"role": "assistant", "content": "Fabricated "})
            length = len(first) + (20 if self.truncated_body else 0)
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: "
                + str(length).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + first
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


class FragmentedToolPeer:
    def __init__(self) -> None:
        self.request: tuple[str, dict[str, str], dict] | None = None

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            self.request = await read_request(reader)
            frames = [
                stream_event(
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "fabricated-tool-id",
                                "type": "function",
                                "function": {"name": "lookup", "arguments": '{"count":'},
                            }
                        ],
                    }
                ),
                stream_event(
                    {"tool_calls": [{"index": 0, "function": {"arguments": "2}"}}]},
                    "tool_calls",
                    created=2,
                ),
                b"data: [DONE]\n\n",
            ]
            payload = b"".join(frames)
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\nConnection: close\r\n\r\n"
            )
            for frame in frames:
                midpoint = len(frame) // 2
                writer.write(frame[:midpoint])
                await writer.drain()
                writer.write(frame[midpoint:])
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


class CancelledStreamPeer:
    def __init__(self) -> None:
        self.closed = asyncio.Event()
        self.requests = 0

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await read_request(reader)
            self.requests += 1
            first = stream_event({"role": "assistant", "content": "Fabricated "})
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
                + hex(len(first))[2:].encode()
                + b"\r\n"
                + first
                + b"\r\n"
            )
            await writer.drain()
            assert await reader.read() == b""
            self.closed.set()
        finally:
            writer.close()
            await writer.wait_closed()


def assert_exchange(
    seen: list[tuple[str, dict[str, str], dict]],
    credential: FreshCredential,
    recorder: ListAttemptRecorder,
    response: ModelResponse,
    request: ModelRequest,
) -> None:
    assert response.content == "Fabricated answer."
    assert response.request_id == "fabricated-success"
    assert response.receipt is not None
    assert response.receipt.response_id == "fabricated-body-id"
    assert len(seen) == credential.reads == len(recorder.started) == 2
    first_line, first_headers, first_body = seen[0]
    second_line, second_headers, second_body = seen[1]
    assert first_line == second_line == "POST /v1/chat/completions HTTP/1.1"
    assert first_body == second_body
    assert first_body["model"] == "fabricated-wire-model"
    assert first_headers["accept-encoding"] == second_headers["accept-encoding"] == "identity"
    assert first_headers["authorization"] != second_headers["authorization"]
    first_tags = json.loads(first_headers["x-tether-request-tags"])
    second_tags = json.loads(second_headers["x-tether-request-tags"])
    assert first_tags["attempt_id"] != second_tags["attempt_id"]
    assert first_tags["principal"] == second_tags["principal"] == request.tags.principal
    assert recorder.observed[0][1].provider_request_id == "fabricated-failed"
    assert recorder.observed[-1][1].provider_request_id == "fabricated-success"


def test_owned_loopback_surfaces_handler_failure_and_joins_handler() -> None:
    async def scenario() -> None:
        sentinel = RuntimeError("synthetic_handler_failure")

        async def fail_handler(reader, writer):
            raise sentinel

        owned = OwnedLoopback(fail_handler)
        async with owned:
            _, client_writer = await asyncio.open_connection("127.0.0.1", owned.port)
            owned.writers.add(client_writer)
            with pytest.raises(RuntimeError) as failure:
                await owned.wait_event(asyncio.Event())
            assert failure.value is sentinel
        assert owned.handlers
        assert all(task.done() for task in owned.handlers)
        assert client_writer.is_closing()

    asyncio.run(scenario())


def test_owned_loopback_retains_physical_cleanup_through_repeated_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        owned, client_writer = await _held_cleanup_owner()
        acknowledgements: asyncio.Queue[None] = asyncio.Queue()
        original_shield = asyncio.shield

        async def acknowledged_shield(awaitable):
            try:
                return await original_shield(awaitable)
            except asyncio.CancelledError:
                acknowledgements.put_nowait(None)
                raise

        monkeypatch.setattr(asyncio, "shield", acknowledged_shield)
        exit_task = asyncio.create_task(owned.__aexit__(None, None, None))
        try:
            await owned.wait_event(owned.cleanup_started, exit_task)
            handler_task = next(iter(owned.handlers))
            await _cancel_cleanup_twice(
                owned, exit_task, handler_task, client_writer, acknowledgements
            )
            owned.release_cleanup.set()
            with pytest.raises(asyncio.CancelledError):
                await exit_task
            _assert_cleanup_finished(owned, handler_task, client_writer)
        finally:
            finalizer = asyncio.create_task(_settle_owned_loopback(owned, exit_task))
            await loopback_lifetime_controls.await_retained(finalizer)

    asyncio.run(scenario())


async def _held_cleanup_owner():
    handler_started = asyncio.Event()

    async def wait_for_client_eof(reader, writer):
        handler_started.set()
        try:
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()

    owned = HeldCleanupLoopback(wait_for_client_eof)
    try:
        await owned.__aenter__()
        _, client_writer = await asyncio.open_connection("127.0.0.1", owned.port)
        owned.writers.add(client_writer)
        await owned.wait_event(handler_started)
    except BaseException:
        owned.release_cleanup.set()
        cleanup = asyncio.create_task(owned._cleanup())
        await loopback_lifetime_controls.await_retained(cleanup)
        raise

    return owned, client_writer


async def _cancel_cleanup_twice(owned, exit_task, handler_task, client_writer, acknowledgements):
    for _ in range(2):
        exit_task.cancel()
        ack = asyncio.create_task(acknowledgements.get())
        try:
            done, _ = await asyncio.wait((ack, exit_task), return_when=asyncio.FIRST_COMPLETED)
            if exit_task in done:
                await exit_task
                raise AssertionError("cleanup_caller_ended_before_cancel_ack")
            await ack
        finally:
            if not ack.done():
                ack.cancel()
            await asyncio.gather(ack, return_exceptions=True)
        assert not exit_task.done()
        assert owned.cleanup_task is not None
        assert not owned.cleanup_task.done()
        assert not handler_task.done()
        assert not client_writer.is_closing()


def _assert_cleanup_finished(owned, handler_task, client_writer) -> None:
    assert owned.cleanup_task is not None
    assert owned.cleanup_task.done()
    assert owned.server is not None
    assert not owned.server.is_serving()
    assert handler_task.done()
    assert client_writer.is_closing()


async def _settle_owned_loopback(owned, exit_task) -> None:
    failures: list[BaseException] = []
    owned.release_cleanup.set()
    exit_outcomes = await asyncio.gather(exit_task, return_exceptions=True)
    failures.extend(
        error
        for error in exit_outcomes
        if isinstance(error, BaseException) and not isinstance(error, asyncio.CancelledError)
    )
    if owned.cleanup_task is not None:
        cleanup_outcomes = await asyncio.gather(owned.cleanup_task, return_exceptions=True)
        failures.extend(
            error
            for error in cleanup_outcomes
            if isinstance(error, BaseException) and not isinstance(error, asyncio.CancelledError)
        )
    if failures:
        raise BaseExceptionGroup("owned_loopback_settlement_failed", failures)


def test_real_loopback_retry_refreshes_identity_and_receipts(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    async def scenario() -> None:
        peer = LoopbackPeer()
        async with OwnedLoopback(peer.handle) as owned:
            credential = FreshCredential()
            recorder = model_request.attempts
            assert isinstance(recorder, ListAttemptRecorder)
            model = OpenAICompatModel(
                base_url=f"http://127.0.0.1:{owned.port}/v1",
                tiers={"demo": "fabricated-wire-model"},
                limits=limits,
                output_limit_field="max_tokens",
            )
            owned.model = model
            response = await owned.wait(
                complete(
                    model,
                    model_request.model_copy(
                        update={"identity_mode": "caller", "credential": credential}
                    ),
                )
            )
            assert_exchange(peer.seen, credential, recorder, response, model_request)

    asyncio.run(scenario())


def test_real_loopback_cancelled_retry_delay_never_sends_second_request(
    model_request: ModelRequest, limits: ModelExecutionLimits, monkeypatch
) -> None:
    async def scenario() -> None:
        peer = RetryAfterPeer()
        observed = asyncio.Event()

        class Recorder(ListAttemptRecorder):
            async def observe(self, *args, **kwargs):
                await super().observe(*args, **kwargs)
                observed.set()

        recorder = Recorder(model_request.tags)
        async with OwnedLoopback(peer.handle) as owned:
            model = OpenAICompatModel(
                base_url=f"http://127.0.0.1:{owned.port}/v1",
                tiers={"demo": "fabricated-wire-model"},
                limits=limits.model_copy(update={"max_retry_delay_seconds": 30}),
                output_limit_field="max_tokens",
            )
            owned.model = model
            request = model_request.model_copy(update={"attempts": recorder})
            retry_delay = []
            original_sleep = asyncio.sleep

            async def observed_sleep(seconds, result=None):
                retry_delay.append(seconds)
                if seconds == 30:
                    retry_sleep_started.set()
                return await original_sleep(seconds, result)

            retry_sleep_started = asyncio.Event()
            monkeypatch.setattr(
                compatible_module,
                "asyncio",
                SimpleNamespace(timeout=asyncio.timeout, sleep=observed_sleep),
            )
            task = owned.own_task(asyncio.create_task(complete(model, request)))
            try:
                await owned.wait_event(observed, task)
                await owned.wait_event(retry_sleep_started, task)
                assert retry_delay == [30]
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await owned.wait(task)
            finally:
                monkeypatch.undo()
            assert peer.requests == 1
            assert len(recorder.started) == 1

    asyncio.run(scenario())


def test_real_loopback_stream_discloses_text_before_terminal(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    async def scenario() -> None:
        peer = StreamingPeer()
        async with OwnedLoopback(peer.handle) as owned:
            model = OpenAICompatModel(
                base_url=f"http://127.0.0.1:{owned.port}/v1",
                tiers={"demo": "fabricated-wire-model"},
                limits=limits,
                output_limit_field="max_tokens",
            )
            owned.model = model
            stream = owned.own_stream(cast(AsyncGenerator, model.stream(model_request)))
            first = await owned.wait(anext(stream))
            assert isinstance(first, TextDelta)
            assert first.text == "Fabricated "
            assert peer.first_sent.is_set()
            assert not peer.release.is_set()
            peer.release.set()

            async def collect_remaining():
                return [item async for item in stream]

            remaining = await owned.wait(collect_remaining())
            assert [item.text for item in remaining if isinstance(item, TextDelta)] == ["answer."]
            assert isinstance(remaining[-1], ModelResponse)
            assert remaining[-1].content == "Fabricated answer."
            assert remaining[-1].request_id == "fabricated-stream-header"
            assert peer.request is not None
            assert peer.request[2]["stream"] is True

    asyncio.run(scenario())


def test_real_loopback_fragmented_tool_arguments_finish_once(
    model_request: ModelRequest, limits: ModelExecutionLimits, tool_spec: ToolSpec
) -> None:
    async def scenario() -> None:
        peer = FragmentedToolPeer()
        async with OwnedLoopback(peer.handle) as owned:
            model = OpenAICompatModel(
                base_url=f"http://127.0.0.1:{owned.port}/v1",
                tiers={"demo": "fabricated-wire-model"},
                limits=limits,
                output_limit_field="max_tokens",
            )
            owned.model = model
            request = model_request.model_copy(update={"tools": (tool_spec,)})

            async def collect():
                return [item async for item in model.stream(request)]

            items = await owned.wait(collect())
            assert len(items) == 1
            response = items[0]
            assert isinstance(response, ModelResponse)
            assert response.finish_reason == "tool_calls"
            assert [(call.id, call.name, call.arguments) for call in response.tool_calls] == [
                ("fabricated-tool-id", "lookup", {"count": 2})
            ]
            assert peer.request is not None
            assert peer.request[2]["stream"] is True
            recorder = model_request.attempts
            assert isinstance(recorder, ListAttemptRecorder)
            assert len(recorder.started) == 1

    asyncio.run(scenario())


def test_real_loopback_stream_close_releases_socket_after_provisional_text(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    async def scenario() -> None:
        peer = CancelledStreamPeer()
        async with OwnedLoopback(peer.handle) as owned:
            model = OpenAICompatModel(
                base_url=f"http://127.0.0.1:{owned.port}/v1",
                tiers={"demo": "fabricated-wire-model"},
                limits=limits,
                output_limit_field="max_tokens",
            )
            owned.model = model
            stream = owned.own_stream(cast(AsyncGenerator, model.stream(model_request)))
            first = await owned.wait(anext(stream))
            assert isinstance(first, TextDelta)
            assert first.text == "Fabricated "
            await stream.aclose()
            await owned.wait_event(peer.closed)
            assert peer.requests == 1
            recorder = model_request.attempts
            assert isinstance(recorder, ListAttemptRecorder)
            assert len(recorder.started) == 1

    asyncio.run(scenario())


def test_real_loopback_stream_deadline_closes_stalled_socket_without_replay(
    model_request: ModelRequest, limits: ModelExecutionLimits, monkeypatch
) -> None:
    async def scenario() -> None:
        peer = CancelledStreamPeer()
        async with OwnedLoopback(peer.handle) as owned:
            timeout_calls = []
            real_timeout = asyncio.timeout

            def selected_timeout(seconds):
                timeout_calls.append(seconds)
                timer = DeferredRequestTimeout(seconds, real_timeout)
                timer_contexts.append(timer)
                return timer

            timer_contexts = []
            monkeypatch.setattr(
                compatible_module,
                "asyncio",
                SimpleNamespace(timeout=selected_timeout, sleep=asyncio.sleep),
            )
            model = OpenAICompatModel(
                base_url=f"http://127.0.0.1:{owned.port}/v1",
                tiers={"demo": "fabricated-wire-model"},
                limits=limits,
                output_limit_field="max_tokens",
            )
            owned.model = model
            request = model_request.model_copy(update={"timeout_seconds": 1.0})
            stream = owned.own_stream(cast(AsyncGenerator, model.stream(request)))
            events: asyncio.Queue = asyncio.Queue()

            async def forward_stream() -> None:
                async for item in stream:
                    await events.put(item)

            driver = owned.own_task(asyncio.create_task(forward_stream()))
            first = await owned.wait(events.get(), driver)
            assert isinstance(first, TextDelta)
            assert first.text == "Fabricated "
            assert timeout_calls == [1.0]
            assert peer.requests == 1
            assert peer.closed.is_set() is False
            timer_contexts[0].arm()
            with pytest.raises(ModelUnavailable, match="timeout"):
                await owned.wait(events.get(), driver)
            await owned.wait_event(peer.closed)
            assert timeout_calls == [1.0]
            assert timer_contexts[0].armed
            assert timer_contexts[0].context.expired()
            recorder = model_request.attempts
            assert isinstance(recorder, ListAttemptRecorder)
            assert len(recorder.started) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("truncated_body", [False, True])
def test_real_loopback_missing_terminal_never_replays_or_finishes(
    model_request: ModelRequest, limits: ModelExecutionLimits, truncated_body: bool
) -> None:
    async def scenario() -> None:
        peer = IncompleteStreamPeer(truncated_body=truncated_body)
        async with OwnedLoopback(peer.handle) as owned:
            model = OpenAICompatModel(
                base_url=f"http://127.0.0.1:{owned.port}/v1",
                tiers={"demo": "fabricated-wire-model"},
                limits=limits,
                output_limit_field="max_tokens",
            )
            owned.model = model
            items = []

            async def collect() -> None:
                async for item in model.stream(model_request):
                    items.append(item)

            with pytest.raises(ModelUnavailable):
                await owned.wait(collect())
            assert [item.text for item in items if isinstance(item, TextDelta)] == ["Fabricated "]
            assert not any(isinstance(item, ModelResponse) for item in items)
            assert peer.requests == 1
            recorder = model_request.attempts
            assert isinstance(recorder, ListAttemptRecorder)
            assert len(recorder.started) == 1

    asyncio.run(scenario())
