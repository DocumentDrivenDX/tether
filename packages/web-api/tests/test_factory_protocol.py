from __future__ import annotations

from typing import cast

from starlette.types import Receive, Scope, Send
from tether_web_api import ApiFactory, ApiSettings, ASGIApp, Authenticator

from tether.client import ClientProvider


class AlternateAsgiApplication:
    async def __call__(self, _scope: Scope, _receive: Receive, _send: Send) -> None:
        return None


def alternate_factory(
    *, clients: ClientProvider, authenticator: Authenticator, settings: ApiSettings
) -> ASGIApp:
    _ = clients, authenticator, settings
    return AlternateAsgiApplication()


def test_factory_contract_accepts_an_asgi_application_without_fastapi() -> None:
    factory: ApiFactory = alternate_factory
    app = factory(
        clients=cast(ClientProvider, object()),
        authenticator=cast(Authenticator, object()),
        settings=ApiSettings(),
    )
    assert isinstance(app, AlternateAsgiApplication)
