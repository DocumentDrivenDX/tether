"""Shared request and accepted-turn primitives for the remote client."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol, Self

import httpx

from tether.client import ClientCapabilities, TurnResult

from .errors import RemoteTransportError
from .settings import RemoteClientSettings
from .wire import decode, problem

_RESULT_POLL_INTERVAL = 0.2


class RetryFeed(RuntimeError):
    pass


class _TurnOperations(Protocol):
    async def turn_result(self, session_id: str, turn_id: str) -> TurnResult | None: ...

    async def cancel_turn(self, session_id: str, turn_id: str) -> bool: ...


class AcceptedTurn:
    def __init__(self, client: _TurnOperations, session_id: str, turn_id: str) -> None:
        self._client = client
        self._session_id = session_id
        self.turn_id = turn_id

    async def result(self) -> TurnResult:
        while True:
            value = await self._client.turn_result(self._session_id, self.turn_id)
            if value is not None:
                return value
            await asyncio.sleep(_RESULT_POLL_INTERVAL)

    async def cancel(self) -> None:
        await self._client.cancel_turn(self._session_id, self.turn_id)


class RemoteOperations:
    """Shared request, credentials, and lifecycle state for remote views."""

    def __init__(
        self,
        settings: RemoteClientSettings,
        http: httpx.AsyncClient,
        *,
        owns_http: bool,
        capabilities: ClientCapabilities,
    ) -> None:
        self._settings = settings
        self._http = http
        self._owns_http = owns_http
        self._capabilities = capabilities
        self._closed = False

    @classmethod
    async def open(
        cls,
        settings: RemoteClientSettings,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> Self:
        owns_http = http_client is None
        http = http_client or httpx.AsyncClient(
            base_url=settings.base_url.rstrip("/"),
            timeout=httpx.Timeout(
                connect=settings.request_timeout_seconds,
                read=settings.request_timeout_seconds,
                write=settings.request_timeout_seconds,
                pool=settings.request_timeout_seconds,
            ),
        )
        client = cls(
            settings,
            http,
            owns_http=owns_http,
            capabilities=ClientCapabilities(
                methods={}, tiers={"live": "unavailable", "archive": "unavailable"}
            ),
        )
        try:
            value = await client._request("GET", "/capabilities")
            client._capabilities = decode(ClientCapabilities, value)
        except BaseException:
            if owns_http:
                await http.aclose()
            raise
        return client

    @property
    def capabilities(self) -> ClientCapabilities:
        return self._capabilities

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> Self:
        if self._closed:
            raise RuntimeError("remote_client_closed")
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    def _path(self, suffix: str) -> str:
        return self._settings.api_prefix + suffix

    async def _headers(self) -> dict[str, str]:
        if self._closed:
            raise RuntimeError("remote_client_closed")
        credential = await self._settings.credential_provider()
        authorization = await credential.authorization()
        return {"Authorization": authorization} if authorization else {}

    async def _request(
        self,
        method: str,
        suffix: str,
        *,
        params: Any = None,
        json_body: Any = None,
    ) -> Any:
        try:
            response = await self._http.request(
                method,
                self._path(suffix),
                params=params,
                json=json_body,
                headers=await self._headers(),
                timeout=self._settings.request_timeout_seconds,
            )
        except httpx.TransportError:
            raise RemoteTransportError("remote_transport_unavailable") from None
        if response.is_error:
            raise problem(response)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            raise RemoteTransportError("remote_response_invalid_json") from None

    async def _model(
        self, method: str, suffix: str, model: Any, *, params: Any = None, json_body: Any = None
    ) -> Any:
        value = await self._request(method, suffix, params=params, json_body=json_body)
        return decode(model, value)

    async def _send_response(
        self, method: str, suffix: str, *, json_body: Any = None
    ) -> httpx.Response:
        try:
            return await self._http.request(
                method,
                self._path(suffix),
                json=json_body,
                headers=await self._headers(),
                timeout=self._settings.request_timeout_seconds,
            )
        except httpx.TransportError:
            raise RemoteTransportError("remote_transport_unavailable") from None
