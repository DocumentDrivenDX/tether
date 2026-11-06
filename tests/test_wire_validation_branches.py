"""Fabricated compatible-response wire frames exercise every bounded refusal."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tether.adapters.pydantic_ai.wire import (
    StreamingWireValidator,
    observed_usage,
    usage_from_wire,
    validate_stream_usage,
    validate_wire,
    wire_call,
)
from tether.types import ModelExecutionLimits, ModelUnavailable, ToolSpec

LIMITS = ModelExecutionLimits(
    max_response_bytes=4096,
    max_tool_argument_bytes=64,
    max_attempts=1,
    request_timeout_seconds=1,
    max_retry_delay_seconds=1,
)
TOOL = ToolSpec(name="echo", description="Echo", input_schema={"type": "object"})


def _body(message: dict[str, Any] | None = None, **top: Any) -> bytes:
    choice = {
        "index": 0,
        "finish_reason": "stop",
        "message": {"role": "assistant", "content": "hi"} | (message or {}),
    }
    return json.dumps({"id": "r1", "choices": [choice], **top}).encode()


def _reason(data: bytes, limits: ModelExecutionLimits = LIMITS) -> str:
    with pytest.raises(ModelUnavailable) as caught:
        validate_wire(data, (TOOL,), limits)
    return caught.value.reason


def test_valid_response_maps_finish_reasons_and_usage() -> None:
    usage = {
        "prompt_tokens": 3,
        "completion_tokens": 2,
        "prompt_tokens_details": {"cached_tokens": 1},
        "completion_tokens_details": {"reasoning_tokens": 1},
    }
    response = validate_wire(_body(usage=usage), (TOOL,), LIMITS)
    assert response.content == "hi"
    assert response.usage is not None
    assert (response.usage.input_tokens, response.usage.cache_read_tokens) == (3, 1)
    for given, expected in (
        ("length", "length"),
        ("content_filter", "filtered"),
        ("odd", "unknown"),
    ):
        data = json.loads(_body())
        data["choices"][0]["finish_reason"] = given
        assert validate_wire(json.dumps(data).encode(), (TOOL,), LIMITS).finish_reason == expected


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"x" * 5000, "response_too_large"),
        (b"not json", "invalid_response"),
        (b'{"choices": []}', "invalid_response"),
        (b'{"choices": [{"index": 1, "message": {}}, {"index": 2}]}', "invalid_response"),
        (_body({"role": "user"}), "invalid_response"),
        (_body({"refusal": "no"}), "unsupported_content"),
        (_body({"extra": 1}), "unsupported_content"),
        (_body({"content": 7}), "unsupported_content"),
        (_body({"tool_calls": [{"id": "c", "type": "x", "function": {}}]}), "invalid_response"),
        (_body(usage={"prompt_tokens": "x", "completion_tokens": 1}), "invalid_response"),
        (_body(usage=5), "invalid_response"),
    ],
)
def test_response_refusals(data: bytes, reason: str) -> None:
    assert _reason(data) == reason


def test_wire_call_bounds_and_shape() -> None:
    call = {"id": "c1", "type": "function", "function": {"name": "echo", "arguments": "{}"}}
    assert wire_call(call, LIMITS).name == "echo"
    big = {**call, "function": {"name": "echo", "arguments": json.dumps({"a": "z" * 100})}}
    with pytest.raises(ModelUnavailable, match="response_too_large"):
        wire_call(big, LIMITS)
    with pytest.raises(ModelUnavailable, match="invalid_response"):
        wire_call({**call, "function": {"name": "echo", "arguments": {}}}, LIMITS)
    with pytest.raises(ModelUnavailable, match="invalid_response"):
        wire_call({**call, "function": "nope"}, LIMITS)


def test_usage_helpers() -> None:
    assert usage_from_wire(None) is None
    with pytest.raises(ModelUnavailable):
        usage_from_wire({"prompt_tokens": 1})
    assert observed_usage(b"[1]") is None
    assert observed_usage(b"\xff") is None
    assert observed_usage(b'{"usage": {"prompt_tokens": 2, "completion_tokens": 1}}') is not None
    validate_stream_usage({"prompt_tokens": 1})
    validate_stream_usage({"total_tokens": None})
    with pytest.raises(ModelUnavailable):
        validate_stream_usage({"prompt_tokens": -1})
    with pytest.raises(ModelUnavailable):
        validate_stream_usage({"completion_tokens": "x"})


def _chunk(delta: dict[str, Any], finish: str | None = None, **over: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": "s1",
        "object": "chat.completion.chunk",
        "created": 10,
        "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return value | over


def _frame(value: object) -> bytes:
    return b"data: " + json.dumps(value).encode() + b"\n\n"


def _feed_all(frames: list[bytes], limits: ModelExecutionLimits) -> None:
    validator = StreamingWireValidator((TOOL,), limits)
    for frame in frames:
        validator.feed(frame)
    validator.finish()


def _feed_reason(frames: list[bytes], limits: ModelExecutionLimits = LIMITS) -> str:
    with pytest.raises(ModelUnavailable) as caught:
        _feed_all(frames, limits)
    return caught.value.reason


def test_stream_assembles_text_and_tool_call() -> None:
    validator = StreamingWireValidator((TOOL,), LIMITS)
    validator.feed(b": comment\n\n")
    validator.feed(_frame(_chunk({"role": "assistant", "content": "he"})))
    validator.feed(_frame(_chunk({"content": "llo"})))
    validator.feed(
        _frame(
            _chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "echo", "arguments": "{"},
                        }
                    ]
                }
            )
        )
    )
    validator.feed(_frame(_chunk({"tool_calls": [{"index": 0, "function": {"arguments": "}"}}]})))
    final = _chunk({}, "tool_calls", created=11)
    final["usage"] = {"prompt_tokens": 1, "completion_tokens": 1}
    validator.feed(_frame(final).replace(b"data: ", b"data:").replace(b"\n\n", b"\r\n\r\n"))
    validator.feed(b"data: [DONE]\n\n")
    validator.feed(b"\n")
    result = validator.finish()
    assert result.content == "hello"
    assert [call.name for call in result.tool_calls] == ["echo"]
    assert result.usage is not None


@pytest.mark.parametrize(
    ("frames", "reason"),
    [
        ([b"event: x\n"], "invalid_response"),
        ([b"data: [DONE]\n\n"], "invalid_response"),
        ([b"data: {bad\n\n"], "invalid_response"),
        ([b"data: []\n\n"], "invalid_response"),
        ([_frame(_chunk({}, extra=1))], "unsupported_content"),
        ([_frame(_chunk({}, id=5))], "invalid_response"),
        ([_frame(_chunk({}, created=True))], "invalid_response"),
        ([_frame(_chunk({}, choices={}))], "invalid_response"),
        ([_frame(_chunk({}, choices=[{}, {}]))], "invalid_response"),
        (
            [_frame(_chunk({}, usage={"prompt_tokens": -1, "completion_tokens": 1}))],
            "invalid_response",
        ),
        ([_frame(_chunk({"content": 5}))], "unsupported_content"),
        ([_frame(_chunk({"role": "user"}))], "invalid_response"),
        ([_frame(_chunk({"extra": 1}))], "unsupported_content"),
        ([_frame({**_chunk({}), "choices": [{"index": 1, "delta": {}}]})], "invalid_response"),
        (
            [_frame({**_chunk({}), "choices": [{"index": 0, "delta": {}, "x": 1}]})],
            "unsupported_content",
        ),
        (
            [_frame(_chunk({}) | {"choices": [{"index": 0, "delta": {}, "finish_reason": 5}]})],
            "invalid_response",
        ),
        ([_frame(_chunk({"tool_calls": 3}))], "invalid_response"),
        ([_frame(_chunk({"tool_calls": [3]}))], "unsupported_content"),
        ([_frame(_chunk({"tool_calls": [{"index": -1}]}))], "invalid_response"),
        ([_frame(_chunk({"tool_calls": [{"index": 0, "id": 3}]}))], "invalid_response"),
        ([_frame(_chunk({"tool_calls": [{"index": 0, "type": "x"}]}))], "unsupported_content"),
        ([_frame(_chunk({"tool_calls": [{"index": 0, "function": 3}]}))], "unsupported_content"),
        (
            [_frame(_chunk({"tool_calls": [{"index": 0, "function": {"name": 3}}]}))],
            "invalid_response",
        ),
        (
            [_frame(_chunk({"tool_calls": [{"index": 0, "function": {"arguments": 3}}]}))],
            "invalid_response",
        ),
        (
            [_frame(_chunk({"tool_calls": [{"index": 0, "function": {"arguments": "x" * 70}}]}))],
            "response_too_large",
        ),
        ([b"x" * 5000], "response_too_large"),
        ([b"data: "], "invalid_response"),
    ],
)
def test_stream_refusals(frames: list[bytes], reason: str) -> None:
    assert _feed_reason(frames) == reason


def test_stream_identity_ordering_and_duplicates() -> None:
    first = _frame(_chunk({"content": "a"}))
    assert _feed_reason([first, _frame(_chunk({"content": "b"}, id="other"))]) == "invalid_response"
    assert _feed_reason([first, _frame(_chunk({"content": "b"}, created=1))]) == "invalid_response"
    done = _frame(_chunk({}, "stop"))
    assert _feed_reason([done, _frame(_chunk({"content": "late"}))]) == "invalid_response"
    assert _feed_reason([done, b"data: [DONE]\n\n", _frame(_chunk({}))]) == "invalid_response"
    assert _feed_reason([done, b"data: [DONE]\n\n", b"data: [DONE]\n\n"]) == "invalid_response"
    assert _feed_reason([first, b"data: [DONE]\n\n"]) == "invalid_response"
    twice_id = [
        _frame(_chunk({"tool_calls": [{"index": 0, "id": "a"}]})),
        _frame(_chunk({"tool_calls": [{"index": 0, "id": "b"}]})),
    ]
    assert _feed_reason(twice_id) == "invalid_response"
    twice_name = [
        _frame(_chunk({"tool_calls": [{"index": 0, "function": {"name": "echo"}}]})),
        _frame(_chunk({"tool_calls": [{"index": 0, "function": {"name": "echo"}}]})),
    ]
    assert _feed_reason(twice_name) == "invalid_response"


def test_stream_incomplete_tool_call_and_usage_only_chunk() -> None:
    nameless = [
        _frame(_chunk({"tool_calls": [{"index": 0, "id": "c"}]})),
        _frame(_chunk({}, "tool_calls")),
        b"data: [DONE]\n\n",
    ]
    assert _feed_reason(nameless) == "invalid_response"
    validator = StreamingWireValidator((TOOL,), LIMITS)
    validator.feed(_frame(_chunk({"content": "ok"}, "stop")))
    validator.feed(
        _frame(_chunk({}, choices=[], usage={"prompt_tokens": 1, "completion_tokens": 1}))
    )
    validator.feed(b"data: [DONE]\n\n")
    assert validator.finish().usage is not None
