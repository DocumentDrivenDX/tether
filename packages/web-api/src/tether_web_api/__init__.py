"""Optional FastAPI adapter for the public Tether client protocol."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from starlette.types import ASGIApp, Receive, Scope, Send

from tether.client import ClientProvider

from .auth import Authenticator
from .errors import ApiFailure
from .settings import ApiSettings
from .wire import ClientCapabilities

if TYPE_CHECKING:
    from fastapi import FastAPI


class ApiFactory(Protocol):
    """Typed factory seam consumed by web and platform carriers."""

    def __call__(
        self, *, clients: ClientProvider, authenticator: Authenticator, settings: ApiSettings
    ) -> ASGIApp: ...


def create_api(
    *, clients: ClientProvider, authenticator: Authenticator, settings: ApiSettings
) -> FastAPI:
    """Load the optional FastAPI runtime only when constructing an app."""
    from .factory import create_api as factory

    return factory(clients=clients, authenticator=authenticator, settings=settings)


__all__ = [
    "ASGIApp",
    "ApiFactory",
    "ApiFailure",
    "ApiSettings",
    "Authenticator",
    "ClientCapabilities",
    "Receive",
    "Scope",
    "Send",
    "create_api",
]
