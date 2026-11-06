"""HTTP implementation of the public client protocol (requires the `remote` extra)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Mapping, Sequence
from contextlib import aclosing
from typing import Any, Literal

import httpx

from tether.client import (
    UNSET,
    Answer,
    AttachmentRef,
    Change,
    Entry,
    HistoryRef,
    ResyncRequired,
    SessionSummary,
    SessionView,
    TurnResult,
    Unset,
)

from .base import AcceptedTurn, RemoteOperations, RetryFeed
from .errors import RemoteProblem, RemoteTransportError
from .reads import RemoteReads
from .wire import bounded_problem as _bounded_problem
from .wire import change as _change
from .wire import committed_cursor as _committed_cursor
from .wire import decode as _decode
from .wire import problem as _problem
from .wire import required_string as _required_string
from .wire import segment as _segment
from .wire import sse_events as _sse_events


class _RemoteWrites(RemoteOperations):
    """Mutation and accepted-turn methods implementing the client contract."""

    async def create(
        self,
        *,
        ref: str | None = None,
        spec: str | None = None,
        title: str | None = None,
        tags: Sequence[str] = (),
        settings: Mapping[str, Any] | None = None,
    ) -> SessionSummary:
        request_id = ref if ref is not None else str(uuid.uuid4())
        return await self._model(
            "POST",
            "/sessions",
            SessionSummary,
            json_body={
                "request_id": request_id,
                "spec": spec,
                "title": title,
                "tags": list(tags),
                "settings": dict(settings or {}),
            },
        )

    async def send(
        self,
        session_id: str,
        text: str,
        *,
        request_id: str | None = None,
        attachments: Sequence[AttachmentRef] = (),
        spec: str | None = None,
    ) -> AcceptedTurn:
        key = request_id if request_id is not None else str(uuid.uuid4())
        value = await self._request(
            "POST",
            f"/sessions/{_segment(session_id)}/turns",
            json_body={
                "request_id": key,
                "text": text,
                "attachments": [item.model_dump(mode="json") for item in attachments],
                "spec": spec,
            },
        )
        return AcceptedTurn(self, session_id, _required_string(value, "turn_id"))

    async def turn_result(self, session_id: str, turn_id: str) -> TurnResult | None:
        value = await self._request(
            "GET", f"/sessions/{_segment(session_id)}/turns/{_segment(turn_id)}"
        )
        return _decode(TurnResult, value) if value is not None else None

    async def cancel_turn(self, session_id: str, turn_id: str) -> bool:
        return bool(
            await self._request(
                "DELETE", f"/sessions/{_segment(session_id)}/turns/{_segment(turn_id)}"
            )
        )

    async def cancel(self, session_id: str) -> bool:
        return bool(await self._request("POST", f"/sessions/{_segment(session_id)}/cancel"))

    async def confirm(
        self, session_id: str, invocation_id: str, answer: Answer
    ) -> AcceptedTurn | None:
        response = await self._send_response(
            "POST",
            f"/sessions/{_segment(session_id)}/confirmations/{_segment(invocation_id)}",
            json_body={"answer": answer.model_dump(mode="json")},
        )
        if response.status_code == 202:
            try:
                value = response.json()
            except ValueError:
                raise RemoteTransportError("remote_response_invalid_json") from None
            return AcceptedTurn(self, session_id, _required_string(value, "turn_id"))
        if response.is_error:
            raise _problem(response)
        try:
            payload = response.json()
        except ValueError:
            raise RemoteTransportError("remote_response_invalid_json") from None
        if not isinstance(payload, dict) or "state" not in payload:
            raise RemoteTransportError("remote_response_invalid_confirmation")
        _decode(SessionView, payload["state"])
        return None

    async def resume(self, session_id: str) -> AcceptedTurn:
        value = await self._request("POST", f"/sessions/{_segment(session_id)}/resume")
        return AcceptedTurn(self, session_id, _required_string(value, "turn_id"))

    async def resolve(
        self,
        session_id: str,
        invocation_id: str,
        outcome: Literal["completed", "not_completed"],
        *,
        reason: str,
    ) -> SessionSummary:
        return await self._model(
            "POST",
            f"/sessions/{_segment(session_id)}/effects/{_segment(invocation_id)}/resolve",
            SessionSummary,
            json_body={"outcome": outcome, "reason": reason},
        )

    async def update(
        self,
        session_id: str,
        *,
        title: str | Unset | None = UNSET,
        tags: Sequence[str] | None = None,
        settings: Mapping[str, Any] | None = None,
    ) -> SessionSummary:
        body: dict[str, Any] = {}
        if title is not UNSET:
            body["title"] = title
        if tags is not None:
            body["tags"] = list(tags)
        if settings is not None:
            body["settings"] = dict(settings)
        return await self._model(
            "PATCH", f"/sessions/{_segment(session_id)}", SessionSummary, json_body=body
        )

    async def close(self, session_id: str) -> SessionSummary:
        return await self._model("POST", f"/sessions/{_segment(session_id)}/close", SessionSummary)

    async def restore(self, session_id: str) -> SessionSummary:
        return await self._model(
            "POST", f"/sessions/{_segment(session_id)}/restore", SessionSummary
        )

    async def link_history(
        self,
        session_id: str,
        message_index: int,
        previous: HistoryRef,
        verified: HistoryRef,
        *,
        key: str,
    ) -> Entry:
        return await self._model(
            "POST",
            f"/sessions/{_segment(session_id)}/history-links",
            Entry,
            json_body={
                "message_index": str(message_index),
                "previous": previous.model_dump(mode="json"),
                "verified": verified.model_dump(mode="json"),
                "key": key,
            },
        )


class _RemoteFeed(RemoteOperations):
    """Reconnecting change-feed implementation."""

    async def follow(
        self, *, session_id: str | None = None, since: int | None = None
    ) -> AsyncIterator[Change]:
        cursor = since
        backoff = 0.1
        while not self._closed:
            try:
                async with aclosing(self._connection(session_id, cursor)) as connection:
                    async for event_id, event_name, payload in connection:
                        item = _change(event_name, payload)
                        cursor = _committed_cursor(cursor, event_id, item)
                        backoff = 0.1
                        yield item
                        if isinstance(item, ResyncRequired):
                            return
            except RemoteProblem:
                raise
            except (httpx.TimeoutException, httpx.NetworkError, RetryFeed):
                pass
            except httpx.TransportError:
                raise RemoteTransportError("remote_transport_unavailable") from None
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 5.0)

    async def _connection(
        self, session_id: str | None, cursor: int | None
    ) -> AsyncGenerator[tuple[str | None, str, Mapping[str, Any]], None]:
        params = {"session_id": session_id} if session_id is not None else {}
        headers = await self._headers()
        if cursor is not None:
            params["since"] = str(cursor)
            headers["Last-Event-ID"] = str(cursor)
        async with self._http.stream(
            "GET",
            self._path("/events"),
            params=params,
            headers=headers,
            timeout=httpx.Timeout(
                connect=self._settings.request_timeout_seconds,
                read=self._settings.request_timeout_seconds,
                write=self._settings.request_timeout_seconds,
                pool=self._settings.request_timeout_seconds,
            ),
        ) as response:
            if response.is_error:
                if response.status_code < 500 and response.status_code != 429:
                    raise await _bounded_problem(response)
                raise RetryFeed("remote_event_stream_retry")
            async for item in _sse_events(response, self._settings):
                yield item


class RemoteClient(RemoteReads, _RemoteWrites, _RemoteFeed):
    """Actor-bound HTTP client; it never owns a Tether host or server resource."""

    pass
