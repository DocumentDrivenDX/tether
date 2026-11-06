from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import cast

import httpx
import pytest

from tether.client import ResyncRequired, SessionRemoved
from tether.client.remote.errors import RemoteProblem, RemoteTransportError
from tether.client.remote.settings import RemoteClientSettings
from tether.client.remote.wire import committed_cursor, problem, sse_events


class Credential:
    async def authorization(self) -> str:
        return ""


async def credentials() -> Credential:
    return Credential()


def settings(
    *,
    max_sse_line_bytes: int = 1_048_576,
    max_sse_event_bytes: int = 1_048_576,
    event_idle_timeout_seconds: float = 45.0,
) -> RemoteClientSettings:
    return RemoteClientSettings(
        "https://example.test",
        credentials,
        max_sse_line_bytes=max_sse_line_bytes,
        max_sse_event_bytes=max_sse_event_bytes,
        event_idle_timeout_seconds=event_idle_timeout_seconds,
    )


@pytest.mark.anyio
async def test_sse_parser_bounds_line_and_event_with_concise_cases() -> None:
    for payload, line_bytes, event_bytes, code in (
        (b"data: 123456789\n\n", 8, 262_144, "remote_event_line_too_large"),
        (b"data:x\ndata:y\n\n", 8, 8, "remote_event_too_large"),
    ):
        response = httpx.Response(200, content=payload)
        with pytest.raises(RemoteTransportError, match=code):
            async for _ in sse_events(
                response,
                settings(max_sse_line_bytes=line_bytes, max_sse_event_bytes=event_bytes),
            ):
                pass


@pytest.mark.anyio
async def test_default_sse_bounds_accept_server_sized_data_line() -> None:
    marker = "a" * 20_000
    response = httpx.Response(200, content=(f'data: {{"marker":"{marker}"}}\n\n').encode())
    events = [event async for event in sse_events(response, settings())]
    assert events[0][2]["marker"] == marker


class StalledStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        await asyncio.Event().wait()
        yield b""


@pytest.mark.anyio
async def test_sse_parser_enforces_idle_deadline() -> None:
    response = httpx.Response(200, stream=StalledStream())
    with pytest.raises(RemoteTransportError, match="remote_event_idle_timeout") as caught:
        async for _ in sse_events(response, settings(event_idle_timeout_seconds=0.01)):
            pass
    assert caught.value.__cause__ is None


def test_unknown_server_problem_code_is_not_trusted() -> None:
    marker = "fabricated-unknown-code"
    response = httpx.Response(400, json={"code": marker, "detail": marker})
    error = problem(response)
    assert isinstance(error, RemoteProblem)
    assert error.code == "remote_request_failed"
    assert marker not in str(error)


def test_remote_settings_reject_credentials_in_origin() -> None:
    marker = "fabricated-origin-secret"
    origin = "https://" + "user" + ":" + marker + "@example.test"
    with pytest.raises(ValueError, match="base_url_must_be_http_origin") as caught:
        RemoteClientSettings(origin, credentials)
    assert marker not in str(caught.value)


@pytest.mark.parametrize("delimiter", ["?", "#"], ids=["empty-query", "empty-fragment"])
def test_remote_settings_reject_empty_query_and_fragment_delimiters(
    delimiter: str,
) -> None:
    with pytest.raises(ValueError, match="base_url_must_not_include_query_or_fragment"):
        RemoteClientSettings(f"https://example.test{delimiter}", credentials)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("max_sse_line_bytes", True, id="boolean-line-bound"),
        pytest.param("max_sse_event_bytes", 0, id="zero-event-bound"),
        pytest.param("event_idle_timeout_seconds", float("inf"), id="infinite-idle-bound"),
        pytest.param("event_idle_timeout_seconds", float("nan"), id="nan-idle-bound"),
    ],
)
def test_remote_settings_reject_disabled_or_nonfinite_feed_bounds(
    field: str, value: object
) -> None:
    if field == "max_sse_line_bytes":
        with pytest.raises(ValueError, match="remote_event_"):
            settings(max_sse_line_bytes=cast(int, value))
    elif field == "max_sse_event_bytes":
        with pytest.raises(ValueError, match="remote_event_"):
            settings(max_sse_event_bytes=cast(int, value))
    else:
        with pytest.raises(ValueError, match="remote_event_"):
            settings(event_idle_timeout_seconds=cast(float, value))


def test_cursor_is_validated_only_at_a_matching_commit_boundary() -> None:
    unfinished = SessionRemoved(seq=12, session_id="s", last_in_commit=False)
    finished = SessionRemoved(seq=13, session_id="s", last_in_commit=True)
    assert committed_cursor(8, None, unfinished) == 8
    assert committed_cursor(8, "13", finished) == 13
    with pytest.raises(RemoteTransportError, match="remote_event_cursor_mismatch"):
        committed_cursor(8, "12", finished)
    with pytest.raises(RemoteTransportError, match="remote_event_cursor_uncommitted"):
        committed_cursor(8, "12", unfinished)
    with pytest.raises(RemoteTransportError, match="remote_event_cursor_missing"):
        committed_cursor(8, None, finished)
    with pytest.raises(RemoteTransportError, match="remote_event_cursor_regressed"):
        committed_cursor(14, "13", finished)
    reset = ResyncRequired(reason="gap")
    assert committed_cursor(8, None, reset) == 8


def test_cursor_rejects_unbounded_decimal_before_integer_conversion() -> None:
    oversized = "9" * 5000
    with pytest.raises(RemoteTransportError, match="remote_event_cursor_invalid"):
        committed_cursor(
            None, oversized, SessionRemoved(seq=1, session_id="s", last_in_commit=True)
        )
