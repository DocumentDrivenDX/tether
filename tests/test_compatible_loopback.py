"""The selected compatible adapter crosses a real local HTTP socket once per attempt."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import cast

import pytest

from tether.adapters.openai_compat import OpenAICompatModel
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


def stream_event(delta: dict, finish: str | None = None) -> bytes:
    frame = {
        "id": "fabricated-stream-id",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "fabricated-wire-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return b"data: " + json.dumps(frame).encode() + b"\n\n"


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


def test_real_loopback_retry_refreshes_identity_and_receipts(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    async def scenario() -> None:
        peer = LoopbackPeer()
        server = await asyncio.start_server(peer.handle, "127.0.0.1", 0)
        credential = FreshCredential()
        recorder = model_request.attempts
        assert isinstance(recorder, ListAttemptRecorder)
        port = server.sockets[0].getsockname()[1]
        model = OpenAICompatModel(
            base_url=f"http://127.0.0.1:{port}/v1",
            tiers={"demo": "fabricated-wire-model"},
            limits=limits,
            output_limit_field="max_tokens",
        )
        try:
            response = await complete(
                model,
                model_request.model_copy(
                    update={"identity_mode": "caller", "credential": credential}
                ),
            )
        finally:
            await model.aclose()
            server.close()
            await server.wait_closed()
        assert_exchange(peer.seen, credential, recorder, response, model_request)

    asyncio.run(scenario())


def test_real_loopback_stream_discloses_text_before_terminal(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    async def scenario() -> None:
        peer = StreamingPeer()
        server = await asyncio.start_server(peer.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        model = OpenAICompatModel(
            base_url=f"http://127.0.0.1:{port}/v1",
            tiers={"demo": "fabricated-wire-model"},
            limits=limits,
            output_limit_field="max_tokens",
        )
        stream = cast(AsyncGenerator, model.stream(model_request))
        try:
            first = await asyncio.wait_for(anext(stream), 2)
            assert isinstance(first, TextDelta)
            assert first.text == "Fabricated "
            assert peer.first_sent.is_set()
            assert not peer.release.is_set()
            peer.release.set()
            remaining = [item async for item in stream]
            assert [item.text for item in remaining if isinstance(item, TextDelta)] == ["answer."]
            assert isinstance(remaining[-1], ModelResponse)
            assert remaining[-1].content == "Fabricated answer."
            assert remaining[-1].request_id == "fabricated-stream-header"
            assert peer.request is not None
            assert peer.request[2]["stream"] is True
        finally:
            peer.release.set()
            await stream.aclose()
            await model.aclose()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_real_loopback_fragmented_tool_arguments_finish_once(
    model_request: ModelRequest, limits: ModelExecutionLimits, tool_spec: ToolSpec
) -> None:
    async def scenario() -> None:
        peer = FragmentedToolPeer()
        server = await asyncio.start_server(peer.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        model = OpenAICompatModel(
            base_url=f"http://127.0.0.1:{port}/v1",
            tiers={"demo": "fabricated-wire-model"},
            limits=limits,
            output_limit_field="max_tokens",
        )
        request = model_request.model_copy(update={"tools": (tool_spec,)})
        try:
            items = [item async for item in model.stream(request)]
        finally:
            await model.aclose()
            server.close()
            await server.wait_closed()
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
        server = await asyncio.start_server(peer.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        model = OpenAICompatModel(
            base_url=f"http://127.0.0.1:{port}/v1",
            tiers={"demo": "fabricated-wire-model"},
            limits=limits,
            output_limit_field="max_tokens",
        )
        stream = cast(AsyncGenerator, model.stream(model_request))
        try:
            first = await asyncio.wait_for(anext(stream), 2)
            assert isinstance(first, TextDelta)
            assert first.text == "Fabricated "
            await asyncio.wait_for(stream.aclose(), 2)
            await asyncio.wait_for(peer.closed.wait(), 2)
        finally:
            await stream.aclose()
            await model.aclose()
            server.close()
            await server.wait_closed()
        assert peer.requests == 1
        recorder = model_request.attempts
        assert isinstance(recorder, ListAttemptRecorder)
        assert len(recorder.started) == 1

    asyncio.run(scenario())


def test_real_loopback_stream_deadline_closes_stalled_socket_without_replay(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    async def scenario() -> None:
        peer = CancelledStreamPeer()
        server = await asyncio.start_server(peer.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        model = OpenAICompatModel(
            base_url=f"http://127.0.0.1:{port}/v1",
            tiers={"demo": "fabricated-wire-model"},
            limits=limits,
            output_limit_field="max_tokens",
        )
        request = model_request.model_copy(update={"timeout_seconds": 1.0})
        stream = cast(AsyncGenerator, model.stream(request))
        try:
            first = await asyncio.wait_for(anext(stream), 2)
            assert isinstance(first, TextDelta)
            assert first.text == "Fabricated "
            with pytest.raises(ModelUnavailable, match="timeout"):
                await asyncio.wait_for(anext(stream), 2)
            await asyncio.wait_for(peer.closed.wait(), 2)
        finally:
            await stream.aclose()
            await model.aclose()
            server.close()
            await server.wait_closed()
        assert peer.requests == 1
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
        server = await asyncio.start_server(peer.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        model = OpenAICompatModel(
            base_url=f"http://127.0.0.1:{port}/v1",
            tiers={"demo": "fabricated-wire-model"},
            limits=limits,
            output_limit_field="max_tokens",
        )
        items = []

        async def collect() -> None:
            async for item in model.stream(model_request):
                items.append(item)

        try:
            with pytest.raises(ModelUnavailable):
                await collect()
        finally:
            await model.aclose()
            server.close()
            await server.wait_closed()
        assert [item.text for item in items if isinstance(item, TextDelta)] == ["Fabricated "]
        assert not any(isinstance(item, ModelResponse) for item in items)
        assert peer.requests == 1
        recorder = model_request.attempts
        assert isinstance(recorder, ListAttemptRecorder)
        assert len(recorder.started) == 1

    asyncio.run(scenario())
