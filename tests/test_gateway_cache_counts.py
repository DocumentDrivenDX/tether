"""Gateway cache counts under the explicitly selected stream dialect, on fabricated frames only.

The usage shapes mirror the field structure observed from gateway backends: top-level
`cache_read_input_tokens`/`cache_creation_input_tokens` with an optional `cache_creation`
retention breakdown, and the nested OpenAI `prompt_tokens_details` fields. Every value is
fabricated.
"""

from __future__ import annotations

import json
from typing import Any

import httpx2 as httpx
import pytest

from tether.adapters.openai_compat import OpenAICompatModel
from tether.adapters.pydantic_ai.gateway import gateway_usage
from tether.adapters.pydantic_ai.wire import (
    GATEWAY_STREAM_PROFILE,
    STRICT_STREAM_PROFILE,
    StreamingWireValidator,
    usage_from_wire,
)
from tether.models import complete
from tether.types import ModelExecutionLimits, ModelRequest, ModelUnavailable, TokenUsage

pytestmark = pytest.mark.anyio

LIMITS = ModelExecutionLimits(
    max_response_bytes=16384,
    max_tool_argument_bytes=64,
    max_attempts=1,
    request_timeout_seconds=5,
    max_retry_delay_seconds=0.001,
)
FLAT = {"cache_read_input_tokens", "cache_creation_input_tokens", "cache_creation"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def flat_usage(read: int = 90, write: int = 0, **extra: Any) -> dict[str, Any]:
    return {
        "prompt_tokens": 100,
        "completion_tokens": 5,
        "total_tokens": 105,
        "cache_read_input_tokens": read,
        "cache_creation_input_tokens": write,
    } | extra


def write_usage() -> dict[str, Any]:
    return flat_usage(
        0,
        90,
        cache_creation={"ephemeral_5m_input_tokens": 90, "ephemeral_1h_input_tokens": 0},
    )


def _chunk(delta: dict[str, Any], usage: dict[str, Any] | None, finish=None) -> dict[str, Any]:
    return {
        "id": "fabricated-completion",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "fabricated-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        "usage": usage,
    }


def frames(final: dict[str, Any]) -> list[dict[str, Any]]:
    interim = final | {"completion_tokens": None, "total_tokens": None}
    return [
        _chunk({"role": "assistant", "content": "fabricated "}, interim),
        _chunk({"content": "answer"}, final, "stop"),
    ]


def sse(values: list[dict[str, Any]]) -> bytes:
    events = b"".join(b"data: " + json.dumps(value).encode() + b"\n\n" for value in values)
    return events + b"data: [DONE]\n\n"


def run(final: dict[str, Any], profile: str = GATEWAY_STREAM_PROFILE):
    validator = StreamingWireValidator((), LIMITS, profile=profile)
    forwarded = validator.feed(sse(frames(final)))
    return validator.finish(), forwarded


def forwarded_usage(data: bytes) -> list[Any]:
    events = [line.removeprefix(b"data: ") for line in data.split(b"\n\n") if line]
    return [json.loads(event)["usage"] for event in events[:-1]]


@pytest.mark.parametrize(("usage", "cache"), [(flat_usage(), (90, 0)), (write_usage(), (0, 90))])
def test_gateway_cache_counts_map_to_the_port_subcounts(usage, cache):
    response, forwarded = run(usage)
    assert response.usage is not None
    assert (response.usage.input_tokens, response.usage.output_tokens) == (100, 5)
    assert (response.usage.cache_read_tokens, response.usage.cache_write_tokens) == cache
    for item in forwarded_usage(forwarded):
        assert not set(item) & FLAT
        assert item["prompt_tokens_details"] == {
            "cached_tokens": cache[0],
            "cache_write_tokens": cache[1],
        }
    # The forwarded frames carry the nested shape, which the strict profile maps identically.
    strict = StreamingWireValidator((), LIMITS)
    strict.feed(forwarded)
    assert strict.finish() == response


def test_strict_profile_leaves_top_level_cache_counts_missing():
    response, forwarded = run(flat_usage(), STRICT_STREAM_PROFILE)
    assert response.usage == TokenUsage(input_tokens=100, output_tokens=5)
    assert forwarded == sse(frames(flat_usage()))
    assert usage_from_wire(flat_usage()) == TokenUsage(input_tokens=100, output_tokens=5)


def test_nested_counts_are_kept_and_matching_twins_accepted():
    nested = {"audio_tokens": 0, "cached_tokens": 90, "cache_write_tokens": 0}
    usage = flat_usage(prompt_tokens_details=nested)
    assert gateway_usage(usage) == {
        "prompt_tokens": 100,
        "completion_tokens": 5,
        "total_tokens": 105,
        "prompt_tokens_details": nested,
    }
    only_nested = {"prompt_tokens": 100, "completion_tokens": 5, "prompt_tokens_details": nested}
    assert gateway_usage(only_nested) == only_nested


def test_usage_without_cache_counts_is_unchanged():
    usage: dict[str, Any] = {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}
    assert gateway_usage(usage) == usage
    assert gateway_usage(None) is None
    response, _ = run(usage)
    assert response.usage == TokenUsage(input_tokens=7, output_tokens=2)


def test_interim_usage_with_null_counts_still_maps_cache_counts():
    interim = flat_usage() | {"completion_tokens": None, "total_tokens": None}
    mapped = gateway_usage(interim)
    assert isinstance(mapped, dict)
    assert mapped["prompt_tokens_details"] == {"cached_tokens": 90, "cache_write_tokens": 0}


def _with(usage: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return usage | changes


@pytest.mark.parametrize(
    ("usage", "reason"),
    [
        (flat_usage(read=101), "invalid_response"),
        (flat_usage(read=60, write=41), "invalid_response"),
        (flat_usage(read=-1), "invalid_response"),
        (flat_usage(write=True), "invalid_response"),
        (_with(flat_usage(), cache_read_input_tokens="90"), "invalid_response"),
        (_with(flat_usage(), prompt_tokens_details={"cached_tokens": 89}), "invalid_response"),
        (_with(flat_usage(), prompt_tokens_details="x"), "invalid_response"),
        (
            _with(flat_usage(read=0), prompt_tokens_details={"cached_tokens": 101}),
            "invalid_response",
        ),
        (_with(write_usage(), cache_creation=[]), "unsupported_content"),
        (
            _with(write_usage(), cache_creation={"ephemeral_5m_input_tokens": 90, "x": 0}),
            "unsupported_content",
        ),
        (
            _with(write_usage(), cache_creation={"ephemeral_5m_input_tokens": -1}),
            "unsupported_content",
        ),
        (
            _with(write_usage(), cache_creation={"ephemeral_5m_input_tokens": 89}),
            "invalid_response",
        ),
    ],
)
def test_malformed_or_inconsistent_cache_counts_refuse(usage, reason):
    with pytest.raises(ModelUnavailable) as caught:
        run(usage)
    assert caught.value.reason == reason


def test_cache_creation_without_a_total_is_dropped():
    usage = write_usage()
    del usage["cache_creation_input_tokens"]
    mapped = gateway_usage(usage)
    assert isinstance(mapped, dict)
    assert "cache_creation" not in mapped
    assert mapped["prompt_tokens_details"] == {"cached_tokens": 0}


async def test_adapter_reports_gateway_cache_counts(model_request: ModelRequest):
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(
            200,
            content=sse(frames(write_usage())),
            headers={"content-type": "text/event-stream", "x-request-id": "fabricated-gw-id"},
        )

    model = OpenAICompatModel(
        base_url="https://fabricated-workspace.invalid/ai-gateway/mlflow/v1",
        tiers={"demo": "fabricated-service"},
        limits=LIMITS,
        output_limit_field="max_tokens",
        supports_parallel_tool_calls=False,
        request_tags_header="Databricks-Ai-Gateway-Request-Tags",
        stream_profile=GATEWAY_STREAM_PROFILE,
        transport=httpx.MockTransport(handler),
    )
    try:
        response = await complete(model, model_request)
    finally:
        await model.aclose()
    assert response.content == "fabricated answer"
    assert response.usage is not None
    assert (response.usage.cache_read_tokens, response.usage.cache_write_tokens) == (0, 90)
