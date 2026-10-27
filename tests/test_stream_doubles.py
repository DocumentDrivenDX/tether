"""Doubles preserve text boundaries; physical identities are regenerated, never cassette keys."""

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import cast

import httpx2 as httpx
import pytest
from test_compatible_adapter import adapter
from test_stream_frames import identity

from tether.models import complete
from tether.models.testing import ListAttemptRecorder, RecordingModel, ReplayModel, ScriptedModel
from tether.types import ModelResponse, ModelUnavailable, TextDelta, TokenUsage, ToolCall

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_script_record_replay_emits_explicit_unicode_boundaries_once(model_request):
    response = ModelResponse(content="é😀", finish_reason="stop")
    scripted = ScriptedModel(
        [TextDelta(text="é"), TextDelta(text=""), TextDelta(text="😀"), response]
    )
    lines = []

    async def writer(line):
        lines.append(line)

    values = [
        item
        async for item in RecordingModel(scripted, writer, fabricated=True).stream(model_request)
    ]
    deltas = [v for v in values if isinstance(v, TextDelta)]
    assert [v.text for v in deltas] == ["é", "", "😀"]
    assert all(v.identity == model_request.attempts.reserved[0] for v in deltas)
    encoded = "".join(lines)
    assert json.loads(lines[1])["stream"] == ["é", "", "😀"]
    assert "attempt_id" not in encoded
    tags = model_request.tags.model_copy(
        update={"session_id": "another", "turn_id": "other", "model_call_id": "fresh"}
    )
    recorder = ListAttemptRecorder(tags)
    changed = model_request.model_copy(update={"tags": tags, "attempts": recorder})
    replayed = [item async for item in ReplayModel(encoded).stream(changed)]
    assert [v.text for v in replayed if isinstance(v, TextDelta)] == ["é", "", "😀"]
    assert all(v.identity == recorder.reserved[0] for v in replayed if isinstance(v, TextDelta))
    assert replayed[-1] == response


async def test_explicit_forged_fixture_claim_is_not_repaired(model_request):
    claim = identity(model_request, session_id="foreign")
    model = ScriptedModel(
        [TextDelta(text="a", identity=claim), ModelResponse(content="a", finish_reason="stop")]
    )
    with pytest.raises(ModelUnavailable, match="invalid_response"):
        await complete(model, model_request)


@pytest.mark.parametrize("pieces", [["wrong"], ["a", "ab"], ["é", "a"]])
async def test_successful_cassette_with_inconsistent_text_refuses_at_load(model_request, pieces):
    from tether.models.canonical import cassette_request, digest, matching_request

    value = {
        "key": digest(matching_request(model_request)),
        "request": cassette_request(model_request),
        "stream": pieces,
        "outcome": {
            "response": ModelResponse(content="abc", finish_reason="stop").model_dump(mode="json")
        },
    }
    encoded = (
        json.dumps({"cassette": 1, "provider": "fabricated"}) + "\n" + json.dumps(value) + "\n"
    )
    with pytest.raises(ValueError, match="cassette_text_mismatch"):
        ReplayModel(encoded)


async def test_native_retry_streams_text_with_successful_explicit_attempt(limits, model_request):
    seen = []
    chunks = [
        {
            "id": "fabricated-stream-id",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "fabricated-wire-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": "Fabricated "},
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "fabricated-stream-id",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "fabricated-wire-model",
            "choices": [{"index": 0, "delta": {"content": "answer."}, "finish_reason": None}],
        },
        {
            "id": "fabricated-stream-id",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "fabricated-wire-model",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        },
        {
            "id": "fabricated-stream-id",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "fabricated-wire-model",
            "choices": [],
            "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
        },
    ]
    payload = b"".join(b"data: " + json.dumps(chunk).encode() + b"\n\n" for chunk in chunks)
    payload += b"data: [DONE]\n\n"

    async def handler(request):
        tags = json.loads(request.headers["x-tether-request-tags"])
        seen.append(tags["attempt_id"])
        return httpx.Response(
            503 if len(seen) == 1 else 201,
            json={} if len(seen) == 1 else None,
            content=None if len(seen) == 1 else payload,
            headers={} if len(seen) == 1 else {"content-type": "text/event-stream"},
        )

    model = adapter(limits, handler)
    try:
        values = [item async for item in model.stream(model_request)]
        assert len(seen) == 2
        assert seen[0] != seen[1]
        deltas = [value for value in values if isinstance(value, TextDelta)]
        assert [delta.text for delta in deltas] == ["Fabricated ", "answer."]
        identities = [delta.identity for delta in deltas]
        assert all(identity is not None for identity in identities)
        assert all(
            identity is not None and identity.attempt_id == seen[1] for identity in identities
        )
        assert all(delta.identity == model_request.attempts.started[-1][0] for delta in deltas)
        assert model_request.attempts.observed[-1][2]["status"] == 201
        assert isinstance(values[-1], ModelResponse)
        assert "".join(delta.text for delta in deltas) == values[-1].content
    finally:
        await model.aclose()


async def test_native_text_is_yielded_before_terminal_transport_chunk(limits, model_request):
    identifier = "fabricated-incremental-id"
    first = {
        "id": identifier,
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "fabricated-wire-model",
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": "Fé"},
                "finish_reason": None,
            }
        ],
    }
    remainder = [
        {
            "id": identifier,
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "fabricated-wire-model",
            "choices": [{"index": 0, "delta": {"content": " second"}, "finish_reason": None}],
        },
        {
            "id": identifier,
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "fabricated-wire-model",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        },
        {
            "id": identifier,
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "fabricated-wire-model",
            "choices": [],
            "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
        },
    ]
    ready = asyncio.Event()
    release = asyncio.Event()
    first_bytes = b"data: " + json.dumps(first, ensure_ascii=False).encode() + b"\n\n"
    split = first_bytes.index(b"\xc3\xa9") + 1

    class DelayedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            ready.set()
            yield first_bytes[:split]
            yield first_bytes[split:]
            await release.wait()
            for chunk in remainder:
                yield b"data: " + json.dumps(chunk).encode() + b"\n\n"
            yield b"data: [DONE]\n\n"

        async def aclose(self):
            release.set()

    def handler(_: httpx.Request):
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=DelayedStream(),
        )

    model = adapter(limits, handler)
    stream = cast(AsyncGenerator, model.stream(model_request))
    try:
        first_item = await asyncio.wait_for(anext(stream), 2)
        assert isinstance(first_item, TextDelta)
        assert first_item.text == "Fé"
        assert ready.is_set()
        assert not release.is_set()
        release.set()
        remaining_items = [item async for item in stream]
        assert [item.text for item in remaining_items if isinstance(item, TextDelta)] == [" second"]
        assert isinstance(remaining_items[-1], ModelResponse)
        assert remaining_items[-1].content == "Fé second"
        assert remaining_items[-1].usage is not None
    finally:
        release.set()
        await model.aclose()


async def test_native_stream_cancellation_closes_the_transport(limits, model_request):
    identifier = "fabricated-cancel-id"
    frame = {
        "id": identifier,
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "fabricated-wire-model",
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": "First"},
                "finish_reason": None,
            }
        ],
    }
    release, closed = asyncio.Event(), asyncio.Event()

    class HangingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"data: " + json.dumps(frame).encode() + b"\n\n"
            await release.wait()

        async def aclose(self):
            closed.set()
            release.set()

    def handler(_: httpx.Request):
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=HangingStream(),
        )

    model = adapter(limits, handler)
    stream = cast(AsyncGenerator, model.stream(model_request))
    try:
        first = await asyncio.wait_for(anext(stream), 2)
        assert isinstance(first, TextDelta)
        assert first.text == "First"
        await stream.aclose()
        assert closed.is_set()
    finally:
        release.set()
        await model.aclose()


async def test_native_stream_rejects_unrecognized_delta_before_disclosure(limits, model_request):
    frame = {
        "id": "fabricated-unsupported-id",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "fabricated-wire-model",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "content": "must not escape",
                    "reasoning_content": "unsupported",
                },
                "finish_reason": None,
            }
        ],
    }

    def handler(_: httpx.Request):
        payload = b"data: " + json.dumps(frame).encode() + b"\n\n"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=payload,
        )

    model = adapter(limits, handler)
    stream = cast(AsyncGenerator, model.stream(model_request))
    try:
        with pytest.raises(ModelUnavailable, match="unsupported_content"):
            await anext(stream)
    finally:
        await stream.aclose()
        await model.aclose()


async def test_native_stream_retains_incremental_tool_call_and_terminal_usage(
    limits, model_request, tool_spec
):
    identifier = "fabricated-tool-stream-id"
    frames = [
        {
            "id": identifier,
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "fabricated-wire-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-fabricated",
                                "type": "function",
                                "function": {"name": "lookup", "arguments": '{"count":'},
                            }
                        ],
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": identifier,
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "fabricated-wire-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "1}"}}]},
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": identifier,
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "fabricated-wire-model",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
        },
        {
            "id": identifier,
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "fabricated-wire-model",
            "choices": [],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        },
    ]

    def handler(_: httpx.Request):
        payload = b"".join(b"data: " + json.dumps(frame).encode() + b"\n\n" for frame in frames)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=payload + b"data: [DONE]\n\n",
        )

    model = adapter(limits, handler)
    request = model_request.model_copy(update={"tools": (tool_spec,)})
    try:
        values = [value async for value in model.stream(request)]
        assert isinstance(values[-1], ModelResponse)
        assert values[-1].content == ""
        assert values[-1].tool_calls == (
            ToolCall(id="call-fabricated", name="lookup", arguments={"count": 1}),
        )
        assert values[-1].finish_reason == "tool_calls"
        assert values[-1].usage == TokenUsage(input_tokens=3, output_tokens=2)
    finally:
        await model.aclose()


async def test_malformed_falsy_identity_is_not_auto_repaired_by_scripted(model_request):
    malformed = TextDelta(text="a").model_copy(update={"identity": {}})
    model = ScriptedModel([malformed, ModelResponse(content="a", finish_reason="stop")])
    with pytest.raises(ModelUnavailable, match="invalid_response"):
        await complete(model, model_request)
