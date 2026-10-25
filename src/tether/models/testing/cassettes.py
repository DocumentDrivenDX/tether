"""Fabricated-only JSONL recording and verified deterministic replay."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from hashlib import sha256
from typing import Literal

from pydantic import Field, JsonValue, ValidationError

from tether.models import checked_stream, managed
from tether.models.canonical import (
    canonical,
    cassette_request,
    copy_request,
    digest,
    matching_request,
)
from tether.ports import ModelPort
from tether.types import (
    ModelRequest,
    ModelResponse,
    ModelUnavailable,
    Payload,
    ResponseReceipt,
    StreamItem,
    TextDelta,
)

from .scripted import observe_fixture, prepare_fixture


class Unavailable(Payload):
    reason: str
    request_id: str | None = None
    receipt: ResponseReceipt | None = None


class Outcome(Payload):
    response: ModelResponse | None = None
    unavailable: Unavailable | None = None


class Record(Payload):
    key: str = Field(pattern=r"^[a-f0-9]{64}$")
    request: dict[str, JsonValue]
    stream: tuple[str, ...]
    outcome: Outcome


class Header(Payload):
    cassette: Literal[1]
    provider: str


def text_digest(parts: tuple[str, ...]) -> bytes:
    hasher = sha256()
    for text in parts:
        for offset in range(0, len(text), 8192):
            hasher.update(text[offset : offset + 8192].encode("utf-8", errors="strict"))
    return hasher.digest()


def record_request(record: Record) -> ModelRequest:
    response = record.outcome.response
    if (
        response is not None
        and any(record.stream)
        and text_digest(record.stream) != text_digest((response.content,))
    ):
        raise ValueError("cassette_text_mismatch")
    try:
        request = ModelRequest.model_validate(record.request)
    except ValidationError:
        raise ValueError("invalid_cassette_request") from None
    if record.request != cassette_request(request):
        raise ValueError("noncanonical_cassette_request")
    if record.key != digest(matching_request(request)):
        raise ValueError("cassette_key_mismatch")
    if (record.outcome.response is None) == (record.outcome.unavailable is None):
        raise ValueError("invalid_cassette_outcome")
    return request


def difference(left: JsonValue, right: JsonValue, path: str = "request") -> str | None:
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(left.keys() | right.keys()):
            if key not in left or key not in right:
                return path + "." + key
            found = difference(left[key], right[key], path + "." + key)
            if found:
                return found
        return None
    if isinstance(left, list) and isinstance(right, list):
        return sequence_difference(left, right, path)
    return None if type(left) is type(right) and left == right else path


def sequence_difference(left: list[JsonValue], right: list[JsonValue], path: str) -> str | None:
    if len(left) != len(right):
        return path + ".length"
    for index, (a, b) in enumerate(zip(left, right, strict=True)):
        found = difference(a, b, path + f"[{index}]")
        if found:
            return found
    return None


class RecordingModel:
    def __init__(
        self,
        inner: ModelPort,
        writer: Callable[[str], Awaitable[None]],
        *,
        fabricated: Literal[True],
    ) -> None:
        if fabricated is not True:
            raise TypeError("fabricated=True is required")
        self.provider = inner.provider
        self._inner = inner
        self._writer = writer
        self._lock = asyncio.Lock()
        self._header_written = False

    async def _append(self, record: Record) -> None:
        async with self._lock:
            if not self._header_written:
                header = Header(cassette=1, provider=self.provider)
                await self._writer(canonical(header.model_dump(mode="json")).decode() + "\n")
                self._header_written = True
            await self._writer(
                canonical(record.model_dump(mode="json", exclude_none=True)).decode() + "\n"
            )

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamItem]:
        request = copy_request(request)
        captured = cassette_request(request)
        key = digest(matching_request(request))
        deltas: list[str] = []
        outcome: Outcome | None = None
        try:
            async with managed(checked_stream(self._inner, request)) as stream:
                async for item in stream:
                    if isinstance(item, TextDelta):
                        deltas.append(item.text)
                    else:
                        outcome = Outcome(response=item.model_copy(deep=True))
                    yield item
        except ModelUnavailable as error:
            outcome = Outcome(
                unavailable=Unavailable(
                    reason=error.reason, request_id=error.request_id, receipt=error.receipt
                )
            )
            await self._append(
                Record(key=key, request=captured, stream=tuple(deltas), outcome=outcome)
            )
            raise
        if outcome is not None:
            await self._append(
                Record(key=key, request=captured, stream=tuple(deltas), outcome=outcome)
            )


class ReplayModel:
    def __init__(self, cassette: str, mode: Literal["sequence", "keyed"] = "sequence") -> None:
        if mode not in ("sequence", "keyed"):
            raise ValueError("unsupported_replay_mode")
        try:
            lines = cassette.splitlines()
            header = Header.model_validate_json(lines[0])
            records = tuple(Record.model_validate_json(line) for line in lines[1:])
        except (ValueError, IndexError):
            raise ValueError("invalid_cassette") from None
        self.provider = header.provider
        self._records = records
        self._requests = tuple(record_request(r) for r in records)
        self._mode = mode
        self._position = 0
        self._keyed: dict[str, Record] = {}
        for record in records:
            if record.key in self._keyed and record != self._keyed[record.key] and mode == "keyed":
                raise ValueError("ambiguous_cassette_key")
            self._keyed[record.key] = record

    def _select(self, request: ModelRequest) -> Record:
        actual = matching_request(request)
        if self._mode == "keyed":
            record = self._keyed.get(digest(actual))
            if record is None:
                field = (
                    difference(matching_request(self._requests[0]), actual)
                    if self._requests
                    else "request.key"
                )
                raise AssertionError("cassette_mismatch: " + (field or "request.key"))
            return record
        if self._position >= len(self._records):
            raise AssertionError("cassette_exhausted")
        record = self._records[self._position]
        expected = matching_request(self._requests[self._position])
        field = difference(expected, actual)
        if field:
            raise AssertionError("cassette_mismatch: " + field)
        self._position += 1
        return record

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamItem]:
        record = self._select(request)
        identity = await prepare_fixture(request)
        for text in record.stream:
            yield TextDelta(text=text, identity=identity)
        response = record.outcome.response
        if response is not None:
            response = response.model_copy(deep=True)
            await observe_fixture(request, identity, response, self.provider)
            yield response
        else:
            error = record.outcome.unavailable
            if error is None:
                raise ValueError("invalid_cassette_outcome")
            if error.receipt is not None and request.attempts is not None and identity is not None:
                await request.attempts.observe(identity, error.receipt)
            raise ModelUnavailable(error.reason, error.request_id, error.receipt)
