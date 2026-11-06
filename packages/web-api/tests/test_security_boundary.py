from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import cast

import httpx
import pytest
from fastapi import Request
from pydantic import ValidationError
from starlette.types import ASGIApp, Message, Receive, Scope
from tether_web_api import ApiSettings, create_api

from tether.client import Actor, ClientProvider, SessionSummary


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _invoke_http(app: ASGIApp, receive: Receive) -> list[Message]:
    sent: list[Message] = []
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/sessions",
        "raw_path": b"/api/v1/sessions",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test")],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }

    async def send(message: Message) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return sent


def _response(sent: list[Message]) -> tuple[int, dict[str, object]]:
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(
        cast(bytes, message.get("body", b""))
        for message in sent
        if message["type"] == "http.response.body"
    )
    return cast(int, start["status"]), cast(dict[str, object], json.loads(body))


class ProbeClient:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> SessionSummary:
        self.create_calls.append(kwargs)
        now = datetime.now(UTC)
        return SessionSummary(
            id="s-created",
            owner="alice",
            ref=cast(str | None, kwargs.get("ref")),
            title=cast(str | None, kwargs.get("title")),
            created_at=now,
            updated_at=now,
        )


class ProbeAuth:
    def __init__(self) -> None:
        self.calls = 0

    async def actor_for(self, request: Request) -> Actor:
        self.calls += 1
        _ = request
        return Actor(id="alice")


def test_allowed_origins_are_exact_http_origins() -> None:
    for value in (
        "*",
        "null",
        "ftp://example.test",
        "https://example.test/path",
        "".join(("https://", "user", "@example.test")),
        "https://example.test?query=1",
        "https://example.test#fragment",
        "https://example.test:invalid",
        "https://example.test evil",
    ):
        with pytest.raises(ValueError, match="allowed_origins_must_be_unique_exact_origins"):
            ApiSettings(allowed_origins=(value,))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field", ["heartbeat_seconds", "request_timeout_seconds"])
def test_api_settings_reject_non_finite_timeouts(field: str, value: float) -> None:
    if field == "heartbeat_seconds":
        with pytest.raises(ValidationError):
            ApiSettings(heartbeat_seconds=value)
    else:
        with pytest.raises(ValidationError):
            ApiSettings(request_timeout_seconds=value)


@pytest.mark.anyio
async def test_declared_body_size_is_rejected_before_auth_or_mutation() -> None:
    client = ProbeClient()
    auth = ProbeAuth()
    app = create_api(
        clients=cast(ClientProvider, lambda _actor: client),
        authenticator=auth,
        settings=ApiSettings(max_request_bytes=32),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.post(
            "/api/v1/sessions",
            content=b"{}",
            headers={"content-length": "33"},
        )
    assert response.status_code == 413
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "request_too_large"
    assert auth.calls == 0
    assert client.create_calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("content_length", ["invalid", "2, 2"])
async def test_malformed_or_combined_content_length_is_rejected(content_length: str) -> None:
    client = ProbeClient()
    auth = ProbeAuth()
    app = create_api(
        clients=cast(ClientProvider, lambda _actor: client),
        authenticator=auth,
        settings=ApiSettings(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.post(
            "/api/v1/sessions", content=b"{}", headers={"content-length": content_length}
        )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_content_length"
    assert auth.calls == 0
    assert client.create_calls == []


async def _oversized_chunks() -> AsyncIterator[bytes]:
    yield b'{"request_id":"create-'
    yield b'1","title":"too much"}'


@pytest.mark.anyio
async def test_chunked_body_is_bounded_without_content_length() -> None:
    client = ProbeClient()
    auth = ProbeAuth()
    app = create_api(
        clients=cast(ClientProvider, lambda _actor: client),
        authenticator=auth,
        settings=ApiSettings(max_request_bytes=12),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.post("/api/v1/sessions", content=_oversized_chunks())
    assert response.status_code == 413
    assert response.json()["code"] == "request_too_large"
    assert auth.calls == 0
    assert client.create_calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("origin", ["https://attacker.example", "null"])
async def test_untrusted_origin_is_rejected_before_auth_or_mutation(origin: str) -> None:
    client = ProbeClient()
    auth = ProbeAuth()
    app = create_api(
        clients=cast(ClientProvider, lambda _actor: client),
        authenticator=auth,
        settings=ApiSettings(allowed_origins=("https://trusted.example",)),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.post(
            "/api/v1/sessions",
            json={"request_id": "create-1"},
            headers={"origin": origin},
        )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "origin_denied"
    assert auth.calls == 0
    assert client.create_calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("origin", ["http://test", "https://trusted.example"])
async def test_same_or_explicit_origin_can_mutate(origin: str) -> None:
    client = ProbeClient()
    auth = ProbeAuth()
    app = create_api(
        clients=cast(ClientProvider, lambda _actor: client),
        authenticator=auth,
        settings=ApiSettings(allowed_origins=("https://trusted.example",)),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.post(
            "/api/v1/sessions",
            json={"request_id": "create-1"},
            headers={"origin": origin},
        )
    assert response.status_code == 201
    assert auth.calls == 1
    assert len(client.create_calls) == 1


@pytest.mark.anyio
async def test_bearer_style_mutation_without_origin_remains_supported() -> None:
    client = ProbeClient()
    auth = ProbeAuth()
    app = create_api(
        clients=cast(ClientProvider, lambda _actor: client),
        authenticator=auth,
        settings=ApiSettings(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.post("/api/v1/sessions", json={"request_id": "create-1"})
    assert response.status_code == 201
    assert auth.calls == 1
    assert len(client.create_calls) == 1


@pytest.mark.anyio
async def test_stalled_request_body_times_out_before_auth_or_command_dispatch() -> None:
    client = ProbeClient()
    auth = ProbeAuth()
    app = create_api(
        clients=cast(ClientProvider, lambda _actor: client),
        authenticator=auth,
        settings=ApiSettings(request_timeout_seconds=0.02),
    )
    receive_calls = 0

    async def stalled_receive() -> Message:
        nonlocal receive_calls
        receive_calls += 1
        await asyncio.Future()
        return {"type": "http.disconnect"}

    status, body = _response(await _invoke_http(app, stalled_receive))
    assert status == 408
    assert body["code"] == "request_timeout"
    assert body["retryable"] is True
    assert receive_calls == 1
    assert auth.calls == 0
    assert client.create_calls == []


@pytest.mark.anyio
async def test_empty_request_frames_yield_and_expire_without_auth_or_dispatch() -> None:
    client = ProbeClient()
    auth = ProbeAuth()
    app = create_api(
        clients=cast(ClientProvider, lambda _actor: client),
        authenticator=auth,
        settings=ApiSettings(request_timeout_seconds=0.02),
    )
    receive_calls = 0
    ticker_calls = 0
    stop_ticker = asyncio.Event()

    async def empty_frame_receive() -> Message:
        nonlocal receive_calls
        receive_calls += 1
        return {"type": "http.request", "body": b"", "more_body": True}

    async def ticker() -> None:
        nonlocal ticker_calls
        while not stop_ticker.is_set():
            ticker_calls += 1
            await asyncio.sleep(0)

    ticker_task = asyncio.create_task(ticker())
    try:
        status, body = _response(await _invoke_http(app, empty_frame_receive))
    finally:
        stop_ticker.set()
        await ticker_task
    assert status == 408
    assert body["code"] == "request_timeout"
    assert receive_calls > 0
    assert ticker_calls > 0
    assert auth.calls == 0
    assert client.create_calls == []
