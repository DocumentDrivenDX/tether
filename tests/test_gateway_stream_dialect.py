"""The explicitly selected gateway stream dialect, on fabricated fixture frames only.

The two fixture shapes mirror the field structure observed from two gateway backends (a
content-filtered chat backend and a reasoning-summary backend); every value is fabricated.
"""

from __future__ import annotations

import json
from typing import Any

import httpx2 as httpx
import pytest

from tether.adapters.openai_compat import OpenAICompatModel
from tether.adapters.pydantic_ai.wire import (
    GATEWAY_STREAM_PROFILE,
    STRICT_STREAM_PROFILE,
    StreamingWireValidator,
)
from tether.models import complete
from tether.types import ModelExecutionLimits, ModelRequest, ModelUnavailable

pytestmark = pytest.mark.anyio

LIMITS = ModelExecutionLimits(
    max_response_bytes=16384,
    max_tool_argument_bytes=64,
    max_attempts=1,
    request_timeout_seconds=5,
    max_retry_delay_seconds=0.001,
)
GATEWAY_KEYS = {
    "obfuscation",
    "latency_checkpoint",
    "routing",
    "prompt_filter_results",
    "content_filter_results",
    "content_filter_result",
    "logprobs",
    "refusal",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _frame(value: object) -> bytes:
    return b"data: " + json.dumps(value).encode() + b"\n\n"


def _filters(filtered: bool = False) -> dict[str, Any]:
    return {
        "hate": {"filtered": filtered, "severity": "safe"},
        "jailbreak": {"detected": False, "filtered": False},
        "protected_material_text": {"detected": False, "filtered": False},
    }


def _filtered_chunk(choice: dict[str, Any], **top: Any) -> dict[str, Any]:
    return {
        "id": "fabricated-completion",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "fabricated-model",
        "obfuscation": "fabricated-padding",
        "service_tier": "default",
        "system_fingerprint": None,
        "usage": None,
        "choices": [
            {"index": 0, "logprobs": None, "content_filter_results": {}, "finish_reason": None}
            | choice
        ],
    } | top


def preamble(filtered: bool = False) -> dict[str, Any]:
    return {
        "id": "",
        "object": "",
        "created": 0,
        "model": "",
        "choices": [],
        "prompt_filter_results": [
            {"prompt_index": 0, "content_filter_results": _filters(filtered)}
        ],
    }


def filtered_backend_frames() -> list[dict[str, Any]]:
    """Prompt-filter preamble, filter metadata, a filter error and a trailing latency chunk."""
    trailer = _filtered_chunk({})
    trailer["choices"] = []
    trailer["latency_checkpoint"] = {"service_ttft_ms": 12, "engine_ttlt_ms": 34}
    trailer["routing"] = {"serving_pipereplica": "fabricated-replica"}
    trailer["usage"] = {
        "prompt_tokens": 7,
        "completion_tokens": 2,
        "total_tokens": 9,
        "prompt_tokens_details": {"cached_tokens": 0, "audio_tokens": 0},
        "completion_tokens_details": {"reasoning_tokens": 0, "accepted_prediction_tokens": 0},
    }
    return [
        preamble(),
        _filtered_chunk({"delta": {"role": "assistant", "content": "", "refusal": None}}),
        _filtered_chunk(
            {"delta": {"content": "fabricated "}, "content_filter_results": _filters()}
        ),
        _filtered_chunk(
            {
                "delta": {"content": "answer"},
                "content_filter_result": {
                    "error": {"code": "fabricated_code", "message": "fabricated message"}
                },
            }
        ),
        _filtered_chunk({"delta": {}, "finish_reason": "stop"}),
        trailer,
    ]


def _reasoning_chunk(delta: dict[str, Any], usage: dict[str, Any], finish=None) -> dict[str, Any]:
    return {
        "id": "fabricated-completion",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "fabricated-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        "usage": usage,
    }


def reasoning_part(text: str = "fabricated reasoning") -> dict[str, Any]:
    return {
        "type": "reasoning",
        "summary": [{"type": "summary_text", "text": text, "signature": "fabricated-signature"}],
    }


def reasoning_backend_frames() -> list[dict[str, Any]]:
    """Reasoning-summary parts before text, interim usage with null completion counts."""
    interim = {
        "prompt_tokens": 7,
        "completion_tokens": None,
        "total_tokens": None,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    final = interim | {"completion_tokens": 3, "total_tokens": 10}
    role = {"role": "assistant"}
    return [
        _reasoning_chunk(role | {"content": [reasoning_part()]}, interim),
        _reasoning_chunk(
            role | {"content": [reasoning_part("more fabricated reasoning")]}, interim
        ),
        _reasoning_chunk(role | {"content": "fabricated "}, interim),
        _reasoning_chunk(role | {"content": "answer"}, final, "stop"),
    ]


def run(frames: list[dict[str, Any]], profile: str = GATEWAY_STREAM_PROFILE):
    validator = StreamingWireValidator((), LIMITS, profile=profile)
    forwarded = b"".join(validator.feed(_frame(frame)) for frame in frames)
    forwarded += validator.feed(b"data: [DONE]\n\n")
    return validator.finish(), forwarded


def refusal(frames: list[dict[str, Any]], profile: str = GATEWAY_STREAM_PROFILE) -> str:
    with pytest.raises(ModelUnavailable) as caught:
        run(frames, profile)
    return caught.value.reason


def forwarded_chunks(data: bytes) -> list[Any]:
    events = [line.removeprefix(b"data: ") for line in data.split(b"\n\n") if line]
    assert events[-1] == b"[DONE]"
    return [json.loads(event) for event in events[:-1]]


def keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in keys(item)}
    return set()


@pytest.mark.parametrize("frames", [filtered_backend_frames(), reasoning_backend_frames()])
def test_gateway_dialect_accepts_and_forwards_only_the_strict_shape(frames):
    response, forwarded = run(frames)
    assert response.content == "fabricated answer"
    assert response.finish_reason == "stop"
    assert response.usage is not None
    assert (response.usage.input_tokens, response.usage.output_tokens) in {(7, 2), (7, 3)}
    chunks = forwarded_chunks(forwarded)
    assert all(chunk["object"] == "chat.completion.chunk" for chunk in chunks)
    assert not keys(chunks) & GATEWAY_KEYS
    assert "fabricated reasoning" not in forwarded.decode()
    assert "fabricated-signature" not in forwarded.decode()
    # The forwarded frames are themselves accepted by the strict profile.
    strict = StreamingWireValidator((), LIMITS)
    strict.feed(forwarded)
    assert strict.finish() == response


def test_strict_profile_still_refuses_both_gateway_shapes():
    assert refusal(filtered_backend_frames(), STRICT_STREAM_PROFILE) == "invalid_response"
    without_preamble = filtered_backend_frames()[1:]
    assert refusal(without_preamble, STRICT_STREAM_PROFILE) == "unsupported_content"
    assert refusal(reasoning_backend_frames(), STRICT_STREAM_PROFILE) == "unsupported_content"


def test_strict_profile_forwards_original_bytes_unchanged():
    validator = StreamingWireValidator((), LIMITS)
    raw = _frame(_reasoning_chunk({"role": "assistant", "content": "x"}, None))
    assert validator.feed(raw) is raw


def test_unknown_stream_profile_is_refused():
    with pytest.raises(ValueError, match="unsupported_stream_profile"):
        StreamingWireValidator((), LIMITS, profile="fabricated-profile")


def _mutate(frames: list[dict[str, Any]], index: int, path: tuple[Any, ...], value: Any):
    target: Any = frames[index]
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = value
    return frames


FILTERED = filtered_backend_frames
REASONING = reasoning_backend_frames
CHOICE = ("choices", 0)


@pytest.mark.parametrize(
    ("frames", "reason"),
    [
        (_mutate(FILTERED(), 1, ("unknown_top",), 1), "unsupported_content"),
        (_mutate(FILTERED(), 0, ("unknown_top",), 1), "unsupported_content"),
        (_mutate(FILTERED(), 0, ("choices",), [{}]), "invalid_response"),
        (_mutate(FILTERED(), 0, ("id",), 1), "invalid_response"),
        (_mutate(FILTERED(), 0, ("created",), True), "invalid_response"),
        (_mutate(FILTERED(), 0, ("prompt_filter_results",), {}), "unsupported_content"),
        (_mutate(FILTERED(), 0, ("prompt_filter_results", 0, "extra"), 1), "unsupported_content"),
        (
            _mutate(FILTERED(), 0, ("prompt_filter_results", 0, "prompt_index"), -1),
            "unsupported_content",
        ),
        ([preamble(filtered=True), *FILTERED()[1:]], "prompt_filtered"),
        ([preamble(), *FILTERED()], "invalid_response"),
        ([FILTERED()[1], preamble(), *FILTERED()[2:]], "invalid_response"),
        (_mutate(FILTERED(), 1, (*CHOICE, "logprobs"), {"content": []}), "unsupported_content"),
        (_mutate(FILTERED(), 1, (*CHOICE, "unknown"), None), "unsupported_content"),
        (_mutate(FILTERED(), 1, (*CHOICE, "delta", "refusal"), "no"), "unsupported_content"),
        (_mutate(FILTERED(), 1, (*CHOICE, "delta", "audio"), None), "unsupported_content"),
        (_mutate(FILTERED(), 1, (*CHOICE, "delta"), None), "unsupported_content"),
        (_mutate(FILTERED(), 1, CHOICE, "x"), "unsupported_content"),
        (_mutate(FILTERED(), 2, (*CHOICE, "content_filter_results"), []), "unsupported_content"),
        (
            _mutate(FILTERED(), 2, (*CHOICE, "content_filter_results", "hate", "details"), []),
            "unsupported_content",
        ),
        (
            _mutate(FILTERED(), 2, (*CHOICE, "content_filter_results", "hate", "filtered"), 1),
            "unsupported_content",
        ),
        (
            _mutate(FILTERED(), 2, (*CHOICE, "content_filter_results", "hate", "severity"), 1),
            "unsupported_content",
        ),
        (
            _mutate(FILTERED(), 2, (*CHOICE, "content_filter_results", "hate", "filtered"), True),
            "unsupported_content",
        ),
        (_mutate(FILTERED(), 3, (*CHOICE, "content_filter_result"), {}), "unsupported_content"),
        (
            _mutate(FILTERED(), 3, (*CHOICE, "content_filter_result", "error", "code"), 1),
            "unsupported_content",
        ),
        (_mutate(FILTERED(), 1, ("obfuscation",), 1), "unsupported_content"),
        (
            _mutate(FILTERED(), 5, ("latency_checkpoint", "service_ttft_ms"), -1),
            "unsupported_content",
        ),
        (_mutate(FILTERED(), 5, ("latency_checkpoint",), []), "unsupported_content"),
        (_mutate(FILTERED(), 5, ("routing", "serving_pipereplica"), 1), "unsupported_content"),
        (_mutate(FILTERED(), 1, ("object",), "chat.completion"), "invalid_response"),
        ([*FILTERED()[:1], 1], "invalid_response"),
        (
            _mutate(REASONING(), 0, (*CHOICE, "delta", "content", 0, "type"), "text"),
            "unsupported_content",
        ),
        (
            _mutate(REASONING(), 0, (*CHOICE, "delta", "content", 0, "extra"), 1),
            "unsupported_content",
        ),
        (
            _mutate(REASONING(), 0, (*CHOICE, "delta", "content", 0, "summary"), "x"),
            "unsupported_content",
        ),
        (_mutate(REASONING(), 0, (*CHOICE, "delta", "content", 0), "x"), "unsupported_content"),
        (
            _mutate(REASONING(), 0, (*CHOICE, "delta", "content", 0, "summary", 0, "type"), "x"),
            "unsupported_content",
        ),
        (
            _mutate(REASONING(), 0, (*CHOICE, "delta", "content", 0, "summary", 0, "extra"), 1),
            "unsupported_content",
        ),
        (
            _mutate(REASONING(), 0, (*CHOICE, "delta", "content", 0, "summary", 0, "text"), 1),
            "unsupported_content",
        ),
        (
            _mutate(REASONING(), 0, (*CHOICE, "delta", "content", 0, "summary", 0, "signature"), 1),
            "unsupported_content",
        ),
        (
            _mutate(REASONING(), 0, (*CHOICE, "delta", "content"), [{"type": "text", "text": "x"}]),
            "unsupported_content",
        ),
        (_mutate(REASONING(), 3, ("usage", "completion_tokens"), None), "invalid_response"),
    ],
)
def test_gateway_dialect_refuses_unknown_or_malformed_fields(frames, reason):
    assert refusal(frames) == reason


def test_output_filter_with_content_filter_finish_is_a_filtered_terminal():
    frames = _mutate(FILTERED(), 2, (*CHOICE, "content_filter_results", "hate", "filtered"), True)
    frames = _mutate(frames, 4, (*CHOICE, "finish_reason"), "content_filter")
    response, _ = run(frames)
    assert response.finish_reason == "filtered"


def test_reasoning_summary_without_signature_is_accepted_and_dropped():
    frames = REASONING()
    del frames[0]["choices"][0]["delta"]["content"][0]["summary"][0]["signature"]
    response, forwarded = run(frames)
    assert response.content == "fabricated answer"
    assert b"reasoning" not in forwarded


def sse(frames: list[dict[str, Any]]) -> bytes:
    return b"".join(_frame(frame) for frame in frames) + b"data: [DONE]\n\n"


def adapter(handler, profile: str = GATEWAY_STREAM_PROFILE) -> OpenAICompatModel:
    return OpenAICompatModel(
        base_url="https://fabricated-workspace.invalid/ai-gateway/mlflow/v1",
        tiers={"demo": "fabricated-service"},
        limits=LIMITS,
        output_limit_field="max_tokens",
        supports_parallel_tool_calls=False,
        request_tags_header="Databricks-Ai-Gateway-Request-Tags",
        stream_profile=profile,
        transport=httpx.MockTransport(handler),
    )


def responder(frames: list[dict[str, Any]]):
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(
            200,
            content=sse(frames),
            headers={"content-type": "text/event-stream", "x-request-id": "fabricated-gw-id"},
        )

    return handler


@pytest.mark.parametrize("frames", [filtered_backend_frames(), reasoning_backend_frames()])
async def test_adapter_completes_gateway_shapes_with_selected_profile(
    model_request: ModelRequest, frames
):
    model = adapter(responder(frames))
    try:
        response = await complete(model, model_request)
    finally:
        await model.aclose()
    assert response.content == "fabricated answer"
    assert response.request_id == "fabricated-gw-id"
    assert response.receipt is not None
    assert response.receipt.schema_profile == GATEWAY_STREAM_PROFILE
    assert response.receipt.response_id == "fabricated-completion"


async def test_stream_validation_failure_keeps_request_id_and_receipt(model_request):
    model = adapter(responder(reasoning_backend_frames()), STRICT_STREAM_PROFILE)
    try:
        with pytest.raises(ModelUnavailable) as caught:
            await complete(model, model_request)
    finally:
        await model.aclose()
    assert caught.value.reason == "unsupported_content"
    assert caught.value.request_id == "fabricated-gw-id"
    assert caught.value.receipt is not None
    assert caught.value.receipt.schema_profile == STRICT_STREAM_PROFILE
    assert caught.value.receipt.provider_request_id == "fabricated-gw-id"


def test_adapter_refuses_unknown_stream_profile():
    with pytest.raises(ValueError, match="unsupported_stream_profile"):
        adapter(responder([]), "fabricated-profile")
