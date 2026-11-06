"""Optional explicit CLI-profile credential and model discovery composition."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from httpx import Auth

SERVICE_CALLER_CREDENTIAL_HEADER = "X-Tether-Service-Caller-Authorization"


@dataclass(frozen=True)
class DatabricksCliSettings:
    profile: str
    expected_host: str | None = None


class DatabricksCliProfile:
    """Use only the named CLI profile; never persist its returned credential."""

    def __init__(self, settings: DatabricksCliSettings) -> None:
        from tether.adapters.databricks import CliProfile

        self._source = CliProfile(settings.profile, expected_host=settings.expected_host)

    async def authorization(self) -> str | None:
        return await self._source.authorization()

    async def host(self) -> str:
        return await self._source.host()

    async def models(self) -> tuple[str, ...]:
        return await self._source.models()


def service_caller_auth() -> Auth:
    """Opt into the carrier's explicitly configured service-caller projection.

    The remote client resolves Authorization afresh per request. Apps consumes
    that header at ingress; this additional header lets the carrier verify the
    same caller credential against its trusted forwarded principal. It is never
    an infrastructure credential or a replacement for server-side verification.
    """
    from httpx import Auth, Request, Response

    class Projection(Auth):
        def auth_flow(self, request: Request) -> Generator[Request, Response, None]:
            authorization = request.headers.get("Authorization")
            request.headers.pop(SERVICE_CALLER_CREDENTIAL_HEADER, None)
            if authorization is not None:
                if not authorization.startswith("Bearer "):
                    raise ValueError("databricks_service_caller_requires_bearer")
                request.headers[SERVICE_CALLER_CREDENTIAL_HEADER] = authorization
            yield request

    return Projection()


__all__ = [
    "SERVICE_CALLER_CREDENTIAL_HEADER",
    "DatabricksCliProfile",
    "DatabricksCliSettings",
    "service_caller_auth",
]
