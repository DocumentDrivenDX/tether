"""Fail-closed request identity projection for Databricks Apps ingress."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, cast

from tether_web_api import ApiFailure, Authenticator

from tether.client import Actor, Credential

from .settings import TrustedIngress

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from fastapi import Request


class ActorPreparer(Protocol):
    async def __call__(self, actor: Actor) -> None: ...


@dataclass(frozen=True)
class ServiceCallerIdentity:
    """An embedder-verified service identity and its dedicated Tether owner id."""

    actor_id: str
    principal: str
    forwarded_email: str | None = None


class ServiceCallerAuthenticator(Protocol):
    """Validate one bearer and map its verified principal to a dedicated actor id."""

    async def __call__(self, bearer_token: str, /) -> ServiceCallerIdentity | None: ...


@dataclass(frozen=True)
class RequestContext:
    headers: Mapping[str, str]
    trusted_ingress: TrustedIngress
    local_context: object | None = None


class _RequestCredential(Credential):
    def __init__(self, token: str) -> None:
        self._token = token

    async def authorization(self) -> str:
        return f"Bearer {self._token}"


class DatabricksIdentityResolver:
    """Bind the configured ingress identity to one current request's actor."""

    def __init__(
        self,
        *,
        trusted_ingress: TrustedIngress,
        local_authenticator: Authenticator | None = None,
        prepare_actor: ActorPreparer | None = None,
        service_caller_authenticator: ServiceCallerAuthenticator | None = None,
        service_caller_credential_header: str | None = None,
    ) -> None:
        if trusted_ingress == "local" and local_authenticator is None:
            raise ValueError("local_authenticator_required")
        if service_caller_credential_header is not None and (
            not service_caller_credential_header.startswith("x-")
            or not service_caller_credential_header.isascii()
            or any(
                not (char.islower() or char.isdigit() or char == "-")
                for char in service_caller_credential_header
            )
        ):
            raise ValueError("service_caller_header_invalid")
        self.trusted_ingress: TrustedIngress = trusted_ingress
        self.local_authenticator = local_authenticator
        self.prepare_actor = prepare_actor
        self.service_caller_authenticator = service_caller_authenticator
        self.service_caller_credential_header = service_caller_credential_header

    async def actor_for(self, request: Request) -> Actor:
        if self.trusted_ingress == "local":
            return await self._local_actor(request)

        headers = _header_map(request)
        user = _single(headers, b"x-forwarded-user")
        email = _single(headers, b"x-forwarded-email")
        projected_actor = await self._projected_service_actor(headers, user, email)
        if projected_actor is not None:
            if self.prepare_actor is not None:
                await self.prepare_actor(projected_actor)
            return projected_actor
        if self.service_caller_credential_header is None and not any(
            key in headers
            for key in (
                b"x-forwarded-user",
                b"x-forwarded-email",
                b"x-forwarded-access-token",
            )
        ):
            actor = await self._service_actor(headers)
            if actor is not None:
                if self.prepare_actor is not None:
                    await self.prepare_actor(actor)
                return actor
        token = _single(headers, b"x-forwarded-access-token")
        if (
            user is None
            or email is None
            or token is None
            or not _valid_identity(user)
            or not _valid_email(email)
            or not _valid_token(token)
        ):
            _logger.warning(
                "tether_identity_header_presence forwarded_user=%s forwarded_email=%s "
                "forwarded_access_token=%s authorization=%s",
                b"x-forwarded-user" in headers,
                b"x-forwarded-email" in headers,
                b"x-forwarded-access-token" in headers,
                b"authorization" in headers,
            )
            raise ApiFailure(401, "authentication_required")
        context = _request_context(headers, self.trusted_ingress)
        email = context.headers.get("x-forwarded-email", email)
        actor = Actor(id=email, principal=user, credential=_RequestCredential(token))
        if self.prepare_actor is not None:
            await self.prepare_actor(actor)
        return actor

    async def _local_actor(self, request: Request) -> Actor:
        authenticator = self.local_authenticator
        if authenticator is None:
            raise ApiFailure(401, "authentication_required")
        actor = await authenticator.actor_for(request)
        if self.prepare_actor is not None:
            await self.prepare_actor(actor)
        return actor

    async def _projected_service_actor(
        self,
        headers: Mapping[bytes, list[bytes]],
        user: str | None,
        email: str | None,
    ) -> Actor | None:
        configured_header = (
            self.service_caller_credential_header.encode("ascii")
            if self.service_caller_credential_header is not None
            else None
        )
        projected_header = configured_header or b"x-tether-service-caller-authorization"
        projected_authorization = _single(headers, projected_header)
        if projected_header in headers:
            if configured_header is None:
                _logger.warning("tether_service_caller_projection_not_configured")
                raise ApiFailure(401, "authentication_required")
            conflict = _projected_credential_conflict(
                headers, projected_header, projected_authorization, user, email
            )
            if conflict is not None:
                _logger.warning("tether_service_caller_projection_conflict reason=%s", conflict)
                raise ApiFailure(401, "authentication_required")
            if projected_authorization is None:
                raise ApiFailure(401, "authentication_required")
            identity = await self._service_identity(projected_authorization)
            if identity is None:
                raise ApiFailure(401, "authentication_required")
            if email != identity.forwarded_email:
                _logger.warning(
                    "tether_service_caller_forwarded_projection_mismatch "
                    "forwarded_user_present=%s email_matches_verified_username=%s",
                    user is not None,
                    email == identity.forwarded_email,
                )
                raise ApiFailure(401, "authentication_required")
            _, _, bearer = projected_authorization.partition(" ")
            actor = Actor(
                id=identity.actor_id,
                kind="service",
                principal=identity.principal,
                credential=_RequestCredential(bearer),
            )
            return actor
        return None

    async def _service_actor(
        self,
        headers: Mapping[bytes, list[bytes]],
        *,
        authorization: str | None = None,
    ) -> Actor | None:
        authenticator = self.service_caller_authenticator
        if authenticator is None:
            _logger.warning(
                "tether_service_caller_not_configured forwarded_user=%s forwarded_email=%s "
                "forwarded_access_token=%s authorization=%s",
                b"x-forwarded-user" in headers,
                b"x-forwarded-email" in headers,
                b"x-forwarded-access-token" in headers,
                b"authorization" in headers,
            )
            return None
        authorization = authorization or _single(headers, b"authorization")
        if authorization is None or len(authorization) > 8200:
            _logger.warning(
                "tether_service_caller_missing_bearer forwarded_user=%s "
                "forwarded_email=%s forwarded_access_token=%s authorization=%s",
                b"x-forwarded-user" in headers,
                b"x-forwarded-email" in headers,
                b"x-forwarded-access-token" in headers,
                b"authorization" in headers,
            )
            raise ApiFailure(401, "authentication_required")
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not _valid_token(token):
            _logger.warning(
                "tether_service_caller_invalid_bearer forwarded_user=%s "
                "forwarded_email=%s forwarded_access_token=%s authorization=%s",
                b"x-forwarded-user" in headers,
                b"x-forwarded-email" in headers,
                b"x-forwarded-access-token" in headers,
                True,
            )
            raise ApiFailure(401, "authentication_required")
        identity = await self._service_identity(authorization)
        if identity is None:
            _logger.warning(
                "tether_service_caller_rejected forwarded_user=%s forwarded_email=%s "
                "forwarded_access_token=%s authorization=%s",
                b"x-forwarded-user" in headers,
                b"x-forwarded-email" in headers,
                b"x-forwarded-access-token" in headers,
                True,
            )
            raise ApiFailure(401, "authentication_required")
        if not _valid_identity(identity.actor_id) or not _valid_identity(identity.principal):
            raise ApiFailure(401, "authentication_required")
        return Actor(
            id=identity.actor_id,
            kind="service",
            principal=identity.principal,
            credential=_RequestCredential(token),
        )

    async def _service_identity(self, authorization: str) -> ServiceCallerIdentity | None:
        authenticator = self.service_caller_authenticator
        if authenticator is None:
            return None
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not _valid_token(token):
            return None
        try:
            identity = await authenticator(token)
        except Exception as error:
            _logger.warning(
                "tether_service_caller_provider_error error_type=%s",
                type(error).__name__,
            )
            raise ApiFailure(503, "identity_provider_unavailable", retryable=True) from None
        if (
            identity is None
            or not all(_valid_identity(value) for value in (identity.actor_id, identity.principal))
            or (
                identity.forwarded_email is not None
                and not _valid_identity(identity.forwarded_email)
            )
        ):
            return None
        return identity


def _header_map(request: Request) -> dict[bytes, list[bytes]]:
    raw: object = request.scope.get("headers", ())
    if not isinstance(raw, (list, tuple)):
        return {}
    values: dict[bytes, list[bytes]] = {}
    for item in cast(list[object] | tuple[object, ...], raw):
        pair = cast(list[object] | tuple[object, ...], item)
        if len(pair) != 2:
            return {}
        key, value = pair
        if not isinstance(key, bytes) or not isinstance(value, bytes):
            return {}
        values.setdefault(key.lower(), []).append(value)
    return values


def _single(headers: Mapping[bytes, list[bytes]], key: bytes) -> str | None:
    values = headers.get(key, [])
    if len(values) != 1:
        return None
    try:
        return values[0].decode("ascii")
    except UnicodeDecodeError:
        return None


def _request_context(
    headers: Mapping[bytes, list[bytes]], trusted_ingress: TrustedIngress
) -> RequestContext:
    projected = {
        name.decode("latin-1"): ",".join(value.decode("latin-1") for value in values)
        for name, values in headers.items()
    }
    return RequestContext(headers=MappingProxyType(projected), trusted_ingress=trusted_ingress)


def _valid_identity(value: str) -> bool:
    return (
        0 < len(value) <= 512
        and value == value.strip()
        and "," not in value
        and all(" " < char <= "~" for char in value)
    )


def _projected_credential_conflict(
    headers: Mapping[bytes, list[bytes]],
    header: bytes,
    projected: str | None,
    user: str | None,
    email: str | None,
) -> str | None:
    if projected is None or len(headers.get(header, [])) != 1:
        return "projected_header_ambiguous"
    authorization_conflict = _authorization_conflict(headers, projected)
    if authorization_conflict is not None:
        return authorization_conflict
    return _forwarded_identity_conflict(headers, user, email)


def _forwarded_identity_conflict(
    headers: Mapping[bytes, list[bytes]], user: str | None, email: str | None
) -> str | None:
    if len(headers.get(b"x-forwarded-access-token", [])) > 1:
        return "forwarded_token_ambiguous"
    if (b"x-forwarded-user" in headers and user is None) or (
        b"x-forwarded-email" in headers and email is None
    ):
        return "forwarded_identity_ambiguous"
    if user is None or email is None:
        return "forwarded_identity_missing"
    if not _valid_identity(user):
        return "forwarded_user_invalid"
    if not _valid_identity(email):
        return "forwarded_email_invalid"
    return None


def _authorization_conflict(headers: Mapping[bytes, list[bytes]], projected: str) -> str | None:
    authorization = _single(headers, b"authorization")
    if b"authorization" in headers and authorization is None:
        return "authorization_ambiguous"
    if authorization is not None and authorization != projected:
        return "authorization_mismatch"
    return None


def _valid_token(value: str) -> bool:
    return (
        0 < len(value) <= 8192
        and value == value.strip()
        and "," not in value
        and all("!" <= char <= "~" for char in value)
    )


def _valid_email(value: str) -> bool:
    if not _valid_identity(value) or value.count("@") != 1:
        return False
    local, domain = value.split("@", 1)
    return bool(
        local
        and domain
        and "." in domain
        and not domain.startswith(".")
        and not domain.endswith(".")
    )


__all__ = [
    "ActorPreparer",
    "DatabricksIdentityResolver",
    "RequestContext",
    "ServiceCallerAuthenticator",
    "ServiceCallerIdentity",
]
