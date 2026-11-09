"""Compatible adapter construction, retry and stream paths over a fabricated transport."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any

import httpx2 as httpx
import pytest

from tether.adapters.openai_compat import OpenAICompatModel
from tether.adapters.pydantic_ai.compatible import retry_delay
from tether.models import complete
from tether.types import (
    ModelExecutionLimits,
    ModelRequest,
    ModelResponse,
    ModelUnavailable,
    TextDelta,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _wire(finish: str | None = "stop") -> dict:
    return {
        "id": "fabricated-id",
        "object": "chat.completion",
        "created": 1,
        "model": "fabricated-wire-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish,
                "message": {"role": "assistant", "content": "Fabricated answer."},
            }
        ],
    }


def _sse(*frames: dict) -> bytes:
    return b"".join(b"data: " + json.dumps(f).encode() + b"\n\n" for f in frames) + (
        b"data: [DONE]\n\n"
    )


def _chunk(delta: dict, finish: str | None = None) -> dict:
    return {
        "id": "fabricated-stream",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "fabricated-wire-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def _model(
    limits: ModelExecutionLimits, handler: Callable[[httpx.Request], httpx.Response], **kw: Any
) -> OpenAICompatModel:
    return OpenAICompatModel(
        base_url="https://fabricated.invalid/v1",
        tiers={"demo": "fabricated-wire-model"},
        limits=limits,
        output_limit_field="max_tokens",
        transport=httpx.MockTransport(handler),
        **kw,
    )


@pytest.mark.parametrize(
    "change",
    [
        {"base_url": "https://user:pw@fabricated.invalid"},
        {"base_url": "https://fabricated.invalid/v1?x=1"},
        {"base_url": "https://fabricated.invalid/v1#frag"},
        {"base_url": "https:///nohost"},
        {"tiers": {"demo": ""}},
        {"application_credential": object()},
        {"request_tags_header": "X-Other"},
    ],
)
def test_constructor_refusals(limits: ModelExecutionLimits, change: dict) -> None:
    arguments = {
        "base_url": "https://fabricated.invalid/v1",
        "tiers": {"demo": "wire"},
        "limits": limits,
        "output_limit_field": "max_tokens",
    } | change
    with pytest.raises(ValueError, match=r"invalid_|unsupported_"):
        OpenAICompatModel(**arguments)


def test_retry_delay_honors_numeric_and_http_date_headers() -> None:
    assert retry_delay("0.5", 2.0, 0) == 0.5
    assert retry_delay("90", 2.0, 0) == 2.0
    future = format_datetime(datetime.now(UTC) + timedelta(seconds=600), usegmt=True)
    assert 0 < retry_delay(future, 3.0, 0) <= 3.0
    past = format_datetime(datetime.now(UTC) - timedelta(seconds=600), usegmt=True)
    assert 0 <= retry_delay(past, 0.01, 3) <= 0.01
    assert 0 <= retry_delay(None, 0.01, 0) <= 0.01


async def test_stream_does_not_retry_a_permanent_status(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    calls: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, json={"e": 1})

    model = _model(limits, handler)
    try:
        with pytest.raises(ModelUnavailable, match="http 400"):
            await complete(model, model_request)
    finally:
        await model.aclose()
    assert len(calls) == 1


async def test_unknown_finish_reason_is_returned_as_incomplete_evidence(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    model = _model(limits, lambda _: httpx.Response(200, json=_wire(finish="odd_reason")))
    try:
        response = await complete(model, model_request)
    finally:
        await model.aclose()
    assert response.finish_reason == "unknown"
    assert response.content == "Fabricated answer."


async def test_stream_retries_before_text_and_emits_deltas(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    bodies = iter(
        [
            httpx.Response(503, json={"e": 1}),
            httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=_sse(
                    _chunk({"role": "assistant", "content": "Fabricated "}),
                    _chunk({"content": "answer."}),
                    _chunk({}, "stop"),
                ),
            ),
        ]
    )
    model = _model(limits, lambda _: next(bodies))
    seen = []
    try:
        async for item in model.stream(model_request):
            seen.append(item)
    finally:
        await model.aclose()
    deltas = [item.text for item in seen if isinstance(item, TextDelta)]
    assert "".join(deltas) == "Fabricated answer."
    assert isinstance(seen[-1], ModelResponse)
    assert seen[-1].content == "Fabricated answer."


async def test_stream_does_not_replay_after_text_was_emitted(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    calls: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        # Text arrives, then the stream ends without a terminal marker.
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b"data: "
            + json.dumps(_chunk({"role": "assistant", "content": "Partial"})).encode()
            + b"\n\n",
        )

    model = _model(limits, handler)
    emitted: list[str] = []

    async def consume() -> None:
        async for item in model.stream(model_request):
            if isinstance(item, TextDelta):
                emitted.append(item.text)

    try:
        with pytest.raises(ModelUnavailable):
            await consume()
    finally:
        await model.aclose()
    assert emitted == ["Partial"]
    assert len(calls) == 1


async def test_stream_exhausts_attempts_on_transient_status(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    calls: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(502, json={"e": 1})

    model = _model(limits, handler)
    try:
        with pytest.raises(ModelUnavailable, match="http 502"):
            async for _item in model.stream(model_request):
                pass
    finally:
        await model.aclose()
    assert len(calls) == limits.max_attempts


async def test_stream_refuses_local_request_violations(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    called: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        called.append(1)
        return httpx.Response(200, json=_wire())

    model = _model(limits, handler)
    try:
        with pytest.raises(ModelUnavailable, match="identity_mode_conflict"):
            async for _item in model.stream(
                model_request.model_copy(update={"credential": object()})
            ):
                pass
    finally:
        await model.aclose()
    assert called == []
