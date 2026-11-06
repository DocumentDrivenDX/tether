"""HTTP path, model, event, and problem transformations for remote clients."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncGenerator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

import httpx
from pydantic import TypeAdapter, ValidationError

from tether.client import (
    Change,
    ChangeGap,
    EntriesAppended,
    ExecutionDenied,
    Lagged,
    LeaseLost,
    ReadLimit,
    ReplyReset,
    ReplyText,
    RequestConflict,
    ResolutionConflict,
    ResyncRequired,
    RootBusy,
    SessionClosed,
    SessionRemoved,
    SessionUpdated,
    TurnInProgress,
    UnknownModelCall,
    UnknownSession,
    UnknownTurn,
    Unsupported,
)

from .errors import RemoteProblem, RemoteTransportError
from .settings import RemoteClientSettings

_DECIMAL = re.compile(r"^(0|[1-9][0-9]*)$|^-1$")


def append_param(params: list[tuple[str, str]], key: str, value: str | None) -> None:
    if value is not None:
        params.append((key, value))


def datetime_value(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def segment(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def required_string(value: Any, key: str) -> str:
    payload = cast(dict[str, object], value) if isinstance(value, dict) else {}
    item = payload.get(key)
    if not isinstance(item, str) or not item:
        raise RemoteTransportError("remote_response_missing_identifier")
    return item


def cursor(current: int | None, event_id: str | None) -> int | None:
    if event_id is None:
        return current
    if not event_id.isascii() or not event_id.isdecimal() or not _DECIMAL.fullmatch(event_id):
        raise RemoteTransportError("remote_event_cursor_invalid")
    try:
        return int(event_id)
    except ValueError:
        raise RemoteTransportError("remote_event_cursor_invalid") from None


def committed_cursor(current: int | None, event_id: str | None, change: Change) -> int | None:
    if isinstance(change, ResyncRequired):
        return current
    if isinstance(change, (SessionUpdated, SessionRemoved, EntriesAppended)):
        if change.last_in_commit:
            if event_id is None:
                raise RemoteTransportError("remote_event_cursor_missing")
            value = cursor(current, event_id)
            if value is None or value != change.seq:
                raise RemoteTransportError("remote_event_cursor_mismatch")
            if current is not None and value < current:
                raise RemoteTransportError("remote_event_cursor_regressed")
            return value
        if event_id is not None:
            raise RemoteTransportError("remote_event_cursor_uncommitted")
    elif event_id is not None:
        raise RemoteTransportError("remote_event_cursor_unexpected")
    return current


def problem(response: httpx.Response) -> Exception:
    try:
        raw_payload: object = response.json()
    except ValueError:
        raw_payload = {}
    payload = cast(dict[str, object], raw_payload) if isinstance(raw_payload, dict) else {}
    raw_code = payload.get("code")
    allowed_codes = {
        "unknown_session",
        "unknown_turn",
        "unknown_model_call",
        "denied",
        "unsupported",
        "read_limit",
        "limit_exceeded",
        "capacity_limited",
        "unavailable",
        "request_conflict",
        "resolution_conflict",
        "turn_in_progress",
        "session_closed",
        "resync_required",
        "authentication_required",
        "request_timeout",
        "invalid_request",
    }
    code = (
        raw_code
        if isinstance(raw_code, str) and raw_code in allowed_codes
        else "remote_request_failed"
    )
    retryable = payload.get("retryable") is True
    raw_locator = payload.get("locator")
    locator: dict[str, str] | None = None
    if isinstance(raw_locator, dict):
        candidate = cast(dict[str, object], raw_locator)
        if all(isinstance(value, str) for value in candidate.values()):
            locator = cast(dict[str, str], candidate)
    retry_after = payload.get("retry_after_seconds")
    by_code: dict[str, type[Exception]] = {
        "unknown_session": UnknownSession,
        "unknown_turn": UnknownTurn,
        "unknown_model_call": UnknownModelCall,
        "denied": ExecutionDenied,
        "unsupported": Unsupported,
        "read_limit": ReadLimit,
        "limit_exceeded": ReadLimit,
        "capacity_limited": RootBusy,
        "unavailable": LeaseLost,
        "request_conflict": RequestConflict,
        "resolution_conflict": ResolutionConflict,
        "turn_in_progress": TurnInProgress,
        "session_closed": SessionClosed,
        "resync_required": ChangeGap,
    }
    error_type = by_code.get(code)
    if error_type is ExecutionDenied:
        return ExecutionDenied(code)
    if error_type is not None:
        return error_type(code)
    return RemoteProblem(
        response.status_code,
        code,
        retryable=retryable,
        locator=locator,
        retry_after_seconds=retry_after if isinstance(retry_after, (int, float)) else None,
    )


async def bounded_problem(response: httpx.Response, *, max_bytes: int = 65_536) -> Exception:
    if response.is_stream_consumed:
        return problem(response)
    payload = bytearray()
    async for chunk in response.aiter_bytes():
        if len(payload) + len(chunk) > max_bytes:
            return RemoteProblem(response.status_code, "remote_request_failed")
        payload.extend(chunk)
    return problem(httpx.Response(response.status_code, content=bytes(payload)))


def decode(model: Any, value: Any) -> Any:
    adapter = TypeAdapter(model)
    schema = adapter.json_schema(ref_template="#/$defs/{model}")
    definitions = schema.pop("$defs", {})
    restored = _restore(value, schema, definitions)
    try:
        return adapter.validate_python(restored)
    except ValidationError:
        raise RemoteTransportError("remote_response_invalid") from None


def _resolve(schema: Mapping[str, Any], definitions: Mapping[str, Any]) -> Mapping[str, Any]:
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        return definitions.get(ref.rsplit("/", 1)[1], schema)
    return schema


def _select(schema: Mapping[str, Any], value: Any) -> Mapping[str, Any]:
    raw_choices = schema.get("anyOf", schema.get("oneOf"))
    if not isinstance(raw_choices, list):
        return schema
    choices = cast(list[Mapping[str, Any]], raw_choices)
    selected_type = (
        "null"
        if value is None
        else "object"
        if isinstance(value, Mapping)
        else "array"
        if isinstance(value, list)
        else "integer"
        if type(value) is int
        else "string"
        if isinstance(value, str)
        else None
    )
    selected = next((item for item in choices if item.get("type") == selected_type), None)
    if selected is None and isinstance(value, str) and _DECIMAL.fullmatch(value):
        selected = next((item for item in choices if item.get("type") == "integer"), None)
    return selected or schema


def _restore(value: Any, schema: Mapping[str, Any], definitions: Mapping[str, Any]) -> Any:
    schema = _resolve(schema, definitions)
    schema = _select(schema, value)
    schema = _resolve(schema, definitions)
    kind = schema.get("type")
    if kind == "integer" and isinstance(value, str) and _DECIMAL.fullmatch(value):
        return int(value)
    if kind == "object" and isinstance(value, Mapping):
        props_value = schema.get("properties", {})
        props = cast(Mapping[str, Any], props_value)
        additional = schema.get("additionalProperties")
        source = cast(Mapping[str, Any], value)
        return {
            key: _restore(item, props.get(key, additional), definitions)
            if key in props or isinstance(additional, Mapping)
            else item
            for key, item in source.items()
        }
    if kind == "array" and isinstance(value, list):
        item_schema = schema.get("items", {})
        items = cast(list[Any], value)
        return [_restore(item, item_schema, definitions) for item in items]
    return value


@dataclass
class _SSEFrame:
    event_id: str | None = None
    event_name: str = "message"
    data: list[str] = field(default_factory=lambda: list[str]())
    size: int = 0

    def reset(self) -> None:
        self.event_id = None
        self.event_name = "message"
        self.data.clear()
        self.size = 0


def _frame_line(line: str, frame: _SSEFrame) -> tuple[str | None, str, Mapping[str, Any]] | None:
    if not line:
        if not frame.data:
            frame.reset()
            return None
        try:
            payload = json.loads("\n".join(frame.data))
        except json.JSONDecodeError:
            raise RemoteTransportError("remote_event_invalid_json") from None
        if not isinstance(payload, dict):
            raise RemoteTransportError("remote_event_invalid_payload")
        result = (frame.event_id, frame.event_name, cast(Mapping[str, Any], payload))
        frame.reset()
        return result
    if line.startswith(":"):
        return None
    if line.startswith("id:"):
        frame.event_id = line[3:].lstrip()
    elif line.startswith("event:"):
        frame.event_name = line[6:].lstrip()
    elif line.startswith("data:"):
        frame.data.append(line[5:].lstrip())
    return None


async def sse_events(
    response: httpx.Response,
    settings: RemoteClientSettings,
) -> AsyncGenerator[tuple[str | None, str, Mapping[str, Any]], None]:
    frame = _SSEFrame()
    line_buffer = bytearray()
    iterator = response.aiter_bytes().__aiter__()
    while True:
        chunk = await _next_chunk(iterator, settings.event_idle_timeout_seconds)
        if chunk is None:
            break
        for byte in chunk:
            event = _feed_byte(byte, line_buffer, frame, settings)
            if event is not None:
                yield event
    if line_buffer:
        raise RemoteTransportError("remote_event_incomplete_frame")


async def _next_chunk(iterator: Any, idle_seconds: float) -> bytes | None:
    try:
        async with asyncio.timeout(idle_seconds):
            return await anext(iterator)
    except StopAsyncIteration:
        return None
    except TimeoutError:
        raise RemoteTransportError("remote_event_idle_timeout") from None


def _feed_byte(
    byte: int,
    line_buffer: bytearray,
    frame: _SSEFrame,
    settings: RemoteClientSettings,
) -> tuple[str | None, str, Mapping[str, Any]] | None:
    frame.size += 1
    if byte != 10:
        line_buffer.append(byte)
        if len(line_buffer) > settings.max_sse_line_bytes:
            raise RemoteTransportError("remote_event_line_too_large")
        if frame.size > settings.max_sse_event_bytes:
            raise RemoteTransportError("remote_event_too_large")
        return None
    try:
        line = bytes(line_buffer).removesuffix(b"\r").decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise RemoteTransportError("remote_event_invalid_encoding") from None
    line_buffer.clear()
    event = _frame_line(line, frame)
    if not line:
        frame.size = 0
    return event


_EVENT_MODELS: dict[str, type[Any]] = {
    "session_updated": SessionUpdated,
    "session_removed": SessionRemoved,
    "entries_appended": EntriesAppended,
    "reply_text": ReplyText,
    "reply_reset": ReplyReset,
    "lagged": Lagged,
    "resync_required": ResyncRequired,
}


def change(event_name: str, payload: Mapping[str, Any]) -> Change:
    model = _EVENT_MODELS.get(event_name)
    if model is None:
        raise RemoteTransportError("remote_event_type_unknown")
    if payload.get("type") != event_name:
        raise RemoteTransportError("remote_event_discriminator_invalid")
    return decode(model, {key: value for key, value in payload.items() if key != "type"})
