"""The selected compatible adapter crosses a real local HTTP socket once per attempt."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import cast

from tether.adapters.openai_compat import OpenAICompatModel
from tether.models import complete
from tether.models.testing import ListAttemptRecorder
from tether.types import ModelExecutionLimits, ModelRequest, ModelResponse, TextDelta


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
