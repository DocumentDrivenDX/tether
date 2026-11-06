"""Bounded, reauthorized SSE delivery for ClientProtocol changes."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from tether.client import Actor, ClientProtocol, EntriesAppended, SessionRemoved, SessionUpdated

from ..auth import AuthContext
from ..errors import ApiFailure
from ..serialization import event_data
from ..settings import ApiSettings

_SENTINEL = object()


def _session_id(event: object) -> str | None:
    if isinstance(event, SessionUpdated):
        return event.summary.id
    value = getattr(event, "session_id", None)
    return value if isinstance(value, str) else None


def _durable_sequence(event: object, payload: dict[str, Any]) -> str | None:
    committed = isinstance(event, (SessionUpdated, SessionRemoved, EntriesAppended))
    if committed and getattr(event, "last_in_commit", False):
        return str(payload["seq"])
    return None


def _frame(event_name: str, payload: dict[str, Any], event_id: str | None) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    lines = [f"event: {event_name}"]
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"data: {data}")
    return ("\n".join(lines) + "\n\n").encode("utf-8")


def _same_identity(left: Actor, right: Actor) -> bool:
    return (left.id, left.kind, left.principal) == (right.id, right.kind, right.principal)


async def _current_client(
    auth: AuthContext, request: Request, expected: Actor
) -> ClientProtocol | None:
    actor, client = await auth.resolve(request)
    return client if _same_identity(expected, actor) else None


async def _reauthorize(
    auth: AuthContext,
    request: Request,
    expected: Actor,
    session_id: str | None,
) -> ClientProtocol | None:
    client = await _current_client(auth, request, expected)
    if client is not None and session_id is not None:
        await auth.execute(client.snapshot, session_id)
    return client


async def _authorized_frame(
    event: object, auth: AuthContext, request: Request, actor: Actor, settings: ApiSettings
) -> bytes | None:
    session_id = _session_id(event)
    client = await _reauthorize(auth, request, actor, session_id)
    if client is None:
        return None
    event_name, payload = event_data(event)
    frame = _frame(event_name, dict(payload), _durable_sequence(event, dict(payload)))
    if len(frame) > settings.event_max_bytes:
        return b'event: resync_required\ndata: {"type":"resync_required","reason":"gap"}\n\n'
    return frame


async def _produce(stream: AsyncIterator[object], queue: asyncio.Queue[object]) -> None:
    cancelled = False
    try:
        async for event in stream:
            await queue.put(event)
    except asyncio.CancelledError:
        cancelled = True
        raise
    except Exception as error:
        await queue.put(error)
    finally:
        if not cancelled:
            await queue.put(_SENTINEL)


async def _receive(queue: asyncio.Queue[object], timeout_seconds: float) -> tuple[bool, object]:
    try:
        return True, await asyncio.wait_for(queue.get(), timeout_seconds)
    except TimeoutError:
        return False, _SENTINEL


async def _authorized(
    auth: AuthContext, request: Request, actor: Actor, session_id: str | None
) -> bool:
    return await _reauthorize(auth, request, actor, session_id) is not None


async def _close_stream(producer: asyncio.Task[None], stream: AsyncIterator[object]) -> None:
    producer.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await producer
    close = getattr(stream, "aclose", None)
    if close is not None:
        await close()


async def _generate_stream(
    request: Request,
    stream: AsyncIterator[object],
    auth: AuthContext,
    actor: Actor,
    settings: ApiSettings,
    session_id: str | None,
) -> AsyncIterator[bytes]:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=settings.event_queue_size)
    producer = asyncio.create_task(_produce(stream, queue))
    delivered = 0
    try:
        while not await request.is_disconnected():
            received, value = await _receive(queue, settings.heartbeat_seconds)
            if not received:
                if not await _authorized(auth, request, actor, session_id):
                    return
                yield b": heartbeat\n\n"
                continue
            if value is _SENTINEL or isinstance(value, Exception):
                return
            frame = await _authorized_frame(value, auth, request, actor, settings)
            if frame is None:
                return
            yield frame
            if frame.startswith(b"event: resync_required\n"):
                return
            delivered = (delivered + 1) % settings.event_batch_limit
            if delivered == 0 and not await _authorized(auth, request, actor, session_id):
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        # Authority failures terminate delivery without exposing data or error detail.
        return
    finally:
        await _close_stream(producer, stream)


def build_router(auth: AuthContext, settings: ApiSettings) -> APIRouter:
    router = APIRouter()

    @router.get("/events")
    async def events(
        request: Request,
        session_id: str | None = None,
        since: int | None = None,
    ) -> StreamingResponse:
        actor, client = await auth.resolve(request)
        header_cursor = request.headers.get("last-event-id")
        cursor = since
        if header_cursor is not None:
            if not header_cursor.isascii() or not header_cursor.isdecimal():
                raise ApiFailure(400, "invalid_event_cursor")
            cursor = int(header_cursor)
        if session_id is not None:
            await auth.execute(client.snapshot, session_id)
        stream = client.follow(session_id=session_id, since=cursor)
        return StreamingResponse(
            _generate_stream(request, stream, auth, actor, settings, session_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
