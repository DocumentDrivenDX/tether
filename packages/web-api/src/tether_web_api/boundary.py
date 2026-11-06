"""Bound incoming HTTP bodies and reject cross-origin mutations before auth."""

from __future__ import annotations

import asyncio

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .settings import ApiSettings, is_exact_origin
from .wire import ProblemDetails

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _header_values(scope: Scope, name: bytes) -> list[bytes]:
    return [value for key, value in scope.get("headers", []) if key.lower() == name]


def _length(scope: Scope) -> tuple[int | None, bool]:
    values = _header_values(scope, b"content-length")
    if not values:
        return None, False
    if len(values) != 1:
        return None, True
    raw = values[0]
    if not raw or not raw.isdigit():
        return None, True
    try:
        return int(raw), False
    except ValueError:
        return None, True


def _request_origin(scope: Scope) -> str | None:
    scheme = scope.get("scheme")
    hosts = _header_values(scope, b"host")
    if scheme not in {"http", "https"} or len(hosts) != 1:
        return None
    try:
        host = hosts[0].decode("ascii")
    except UnicodeDecodeError:
        return None
    origin = f"{scheme}://{host}"
    return origin if is_exact_origin(origin) else None


def _origin_allowed(scope: Scope, configured: tuple[str, ...]) -> bool:
    origins = _header_values(scope, b"origin")
    if not origins:
        return True
    if len(origins) != 1:
        return False
    try:
        origin = origins[0].decode("ascii")
    except UnicodeDecodeError:
        return False
    if not is_exact_origin(origin):
        return False
    return origin in configured or origin == _request_origin(scope)


async def _problem(
    scope: Scope,
    receive: Receive,
    send: Send,
    status: int,
    code: str,
    *,
    retryable: bool = False,
) -> None:
    titles = {
        400: "Invalid request",
        403: "Forbidden",
        408: "Request timed out",
        413: "Limit exceeded",
    }
    body = ProblemDetails(
        title=titles[status],
        status=status,
        detail=titles[status],
        code=code,
        retryable=retryable,
    )
    response = JSONResponse(
        body.model_dump(mode="json"),
        status_code=status,
        media_type="application/problem+json",
    )
    await response(scope, receive, send)


async def _bounded_body(
    receive: Receive, limit: int, deadline: float
) -> tuple[bytes | None, bool, bool]:
    chunks: list[bytes] = []
    size = 0
    loop = asyncio.get_running_loop()
    while True:
        if loop.time() >= deadline:
            raise TimeoutError
        message = await receive()
        if loop.time() >= deadline:
            raise TimeoutError
        if message["type"] == "http.disconnect":
            return None, False, True
        if message["type"] != "http.request":
            await asyncio.sleep(0)
            continue
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > limit:
            return None, True, False
        if chunk:
            chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks), False, False
        # ASGI receive implementations can yield empty frames without ever
        # suspending. Cooperate so the absolute deadline and other tasks run.
        await asyncio.sleep(0)


def _early_rejection(scope: Scope, settings: ApiSettings) -> tuple[int, str] | None:
    if scope.get("method") in _UNSAFE_METHODS and not _origin_allowed(
        scope, settings.allowed_origins
    ):
        return 403, "origin_denied"
    content_length, invalid_length = _length(scope)
    if invalid_length:
        return 400, "invalid_content_length"
    if content_length is not None and content_length > settings.max_request_bytes:
        return 413, "request_too_large"
    return None


class RequestBoundary:
    """ASGI guard that keeps parsing bounded and performs origin checks early."""

    def __init__(self, app: ASGIApp, settings: ApiSettings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
        else:
            rejection = _early_rejection(scope, self.settings)
            if rejection is not None:
                await _problem(scope, receive, send, *rejection)
            else:
                loop = asyncio.get_running_loop()
                deadline = loop.time() + self.settings.request_timeout_seconds
                try:
                    async with asyncio.timeout_at(deadline):
                        body, oversized, disconnected = await _bounded_body(
                            receive, self.settings.max_request_bytes, deadline
                        )
                except TimeoutError:
                    await _problem(scope, receive, send, 408, "request_timeout", retryable=True)
                    return
                if disconnected:
                    return
                if oversized or body is None:
                    await _problem(scope, receive, send, 413, "request_too_large")
                else:
                    replayed = False

                    async def replay_body() -> Message:
                        nonlocal replayed
                        if not replayed:
                            replayed = True
                            return {"type": "http.request", "body": body, "more_body": False}
                        return await receive()

                    await self.app(scope, replay_body, send)
