"""Copied stream values and constant-memory text/claimed-attempt consistency."""

import asyncio
from collections.abc import AsyncIterator
from hashlib import sha256
from typing import cast

from tether.types import (
    AttemptIdentity,
    ModelRequest,
    ModelResponse,
    ModelUnavailable,
    Payload,
    ResponseReceipt,
    StreamItem,
    TextDelta,
    TokenUsage,
    ToolCall,
)


def fields(model: type[Payload], value: object) -> dict[str, object]:
    if (
        not isinstance(value, model)
        or set(value.__dict__) != set(model.model_fields)
        or value.__pydantic_extra__
    ):
        raise ValueError("frame_shape")
    return dict(value.__dict__)


def fresh(item: object) -> StreamItem:
    try:
        if isinstance(item, TextDelta):
            value = fields(TextDelta, item)
            if item.identity is not None:
                value["identity"] = fields(AttemptIdentity, item.identity)
            return TextDelta.model_validate(value, strict=True)
        value = fields(ModelResponse, item)
        calls = value["tool_calls"]
        if type(calls) is not tuple:
            raise ValueError("frame_shape")
        value["tool_calls"] = tuple(
            fields(ToolCall, call) for call in cast(tuple[object, ...], calls)
        )
        for key, model in (("usage", TokenUsage), ("receipt", ResponseReceipt)):
            if value[key] is not None:
                value[key] = fields(model, value[key])
        return ModelResponse.model_validate(value, strict=True)
    except (ValueError, TypeError, AttributeError):
        raise ModelUnavailable("invalid_response") from None


async def chunks(text: str) -> AsyncIterator[bytes]:
    for offset in range(0, len(text), 8192):
        yield text[offset : offset + 8192].encode("utf-8", errors="strict")
        await asyncio.sleep(0)


class StreamFrames:
    def __init__(self, request: ModelRequest, maximum: int | None) -> None:
        if maximum is not None and (type(maximum) is not int or maximum < 1):
            raise ValueError("invalid_stream_limit")
        tags = request.tags
        self.scope = (tags.session_id, tags.turn_id, tags.model_call_id, tags.purpose)
        self.maximum = maximum
        self.identity: tuple[str | None, ...] | None = None
        self.seen = False
        self.size = 0
        self.hasher = sha256()

    def _limit(self, size: int) -> None:
        if self.maximum is not None and size > self.maximum:
            raise ModelUnavailable("response_too_large")

    async def check(self, item: StreamItem) -> None:
        try:
            if isinstance(item, TextDelta):
                await self._delta(item)
            else:
                await self._terminal(item)
        except (ValueError, TypeError):
            raise ModelUnavailable("invalid_response") from None

    async def _delta(self, item: TextDelta) -> None:
        if item.identity is not None:
            claimed = item.identity
            if (
                claimed.session_id,
                claimed.turn_id,
                claimed.model_call_id,
                claimed.purpose,
            ) != self.scope:
                raise ModelUnavailable("invalid_response")
            if not claimed.attempt_id:
                raise ModelUnavailable("invalid_response")
        if not item.text:
            return
        state = (
            tuple(getattr(item.identity, key) for key in AttemptIdentity.model_fields)
            if item.identity is not None
            else None
        )
        if self.seen and self.identity != state:
            raise ModelUnavailable("invalid_response")
        self.identity, self.seen = state, True
        async for chunk in chunks(item.text):
            self.size += len(chunk)
            self._limit(self.size)
            self.hasher.update(chunk)

    async def _terminal(self, item: ModelResponse) -> None:
        size, hasher = 0, sha256()
        async for chunk in chunks(item.content):
            size += len(chunk)
            self._limit(size)
            hasher.update(chunk)
        if self.seen and (size != self.size or hasher.digest() != self.hasher.digest()):
            raise ModelUnavailable("invalid_response")
