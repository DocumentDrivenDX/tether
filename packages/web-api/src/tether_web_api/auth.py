"""Application-owned identity seams for API requests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol, TypeVar

if TYPE_CHECKING:
    from fastapi import Request

from tether.client import Actor, ClientProtocol, ClientProvider

from .errors import ApiFailure

T = TypeVar("T")


class Authenticator(Protocol):
    async def actor_for(self, request: Request) -> Actor: ...


class AuthContext:
    """Resolve one current actor and its actor-bound public client."""

    def __init__(
        self, authenticator: Authenticator, clients: ClientProvider, timeout_seconds: float
    ) -> None:
        self._authenticator = authenticator
        self._clients = clients
        self._timeout_seconds = timeout_seconds

    async def resolve(self, request: Request) -> tuple[Actor, ClientProtocol]:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                actor = await self._authenticator.actor_for(request)
        except TimeoutError as error:
            raise ApiFailure(503, "unavailable", retryable=True) from error
        return actor, self._clients(actor)

    async def execute(
        self,
        operation: Callable[..., Awaitable[T]],
        *args: object,
        locator: dict[str, str] | None = None,
        **kwargs: object,
    ) -> T:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await operation(*args, **kwargs)
        except TimeoutError as error:
            code = "outcome_uncertain" if locator else "unavailable"
            raise ApiFailure(503, code, retryable=True, locator=locator) from error
