"""Per-connection Lakebase OAuth for the runnable Apps example only."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from tether.client import Actor
from tether.client.postgres import PostgresDurabilityProfile

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from psycopg import AsyncConnection

    Connection = AsyncConnection[tuple[object, ...]]
else:
    Connection = object


class DatabaseCredential(Protocol):
    token: str | None


class PostgresCredentialApi(Protocol):
    def generate_database_credential(self, endpoint: str) -> DatabaseCredential: ...


class WorkspacePostgresClient(Protocol):
    @property
    def postgres(self) -> PostgresCredentialApi: ...


class UserWorkspaceClientFactory(Protocol):
    def __call__(self, host: str, bearer_token: str, /) -> WorkspacePostgresClient: ...


@dataclass(frozen=True)
class LakebaseSettings:
    workspace_host: str
    endpoint: str
    host: str
    database: str
    infra_role: str
    port: int = 5432
    sslrootcert: str | None = None


def app_service_workspace_client() -> WorkspacePostgresClient:
    """Select the Databricks Apps service principal through the official SDK."""
    from databricks.sdk import WorkspaceClient

    # Apps injects its service-principal OAuth environment. This client is only
    # used by the explicit infrastructure Actor, never as a user fallback.
    # Select only the Apps-injected machine-to-machine credential strategy.
    # An absent Apps credential must fail closed instead of discovering a
    # developer profile, PAT, or unrelated ambient identity.
    return WorkspaceClient(auth_type="oauth-m2m")


class LakebaseConnectionSource:
    """Acquire an OAuth database credential for every physical connection.

    The embedder supplies a role mapper because the database principal name is
    deployment-specific. User credentials are obtained only from the current
    actor; the infrastructure client is selected only for the exact configured
    infrastructure actor id.
    """

    def __init__(
        self,
        settings: LakebaseSettings,
        *,
        database_user: Callable[[Actor], str],
        infrastructure_client: WorkspacePostgresClient,
        user_client_factory: UserWorkspaceClientFactory | None = None,
    ) -> None:
        self._settings = settings
        self.durability_profile = PostgresDurabilityProfile(
            "databricks-lakebase-pg18", endpoint_id=settings.endpoint
        )
        self._database_user = database_user
        self._infrastructure_client = infrastructure_client
        self._user_client_factory = user_client_factory or _sdk_user_client

    @asynccontextmanager
    async def __call__(self, actor: Actor, session_id: str | None, /) -> AsyncIterator[Connection]:
        del session_id
        user = self._database_user(actor)
        if not user or "\x00" in user:
            raise ValueError("invalid_database_principal")
        client = await self._client_for(actor)
        try:
            credential_api = client.postgres
            credential = await _await_database_credential_mint(
                credential_api, self._settings.endpoint
            )
        except Exception as error:
            _log_credential_failure("database_token_mint", error)
            raise PermissionError("database_credential_unavailable") from None
        password = credential.token
        if not isinstance(password, str) or not password:
            _logger.warning(
                "tether_lakebase_credential_failure phase=database_token_empty "
                "exception=none status=none code=none"
            )
            raise PermissionError("database_credential_unavailable")

        from psycopg import AsyncConnection
        from psycopg.conninfo import make_conninfo

        connection_string = make_conninfo(
            host=self._settings.host,
            port=self._settings.port,
            dbname=self._settings.database,
            user=user,
            password=password,
            sslmode="verify-full",
            sslrootcert=self._settings.sslrootcert,
        )
        try:
            connection = await AsyncConnection.connect(connection_string)
        except Exception as error:
            _logger.warning(
                "tether_database_connection_failed kind=%s error_type=%s sqlstate=%s",
                _connection_failure_kind(error),
                type(error).__name__,
                getattr(error, "sqlstate", None),
            )
            raise ConnectionError("database_connection_unavailable") from None
        async with connection:
            yield connection

    async def _client_for(self, actor: Actor) -> WorkspacePostgresClient:
        if actor.id == self._settings.infra_role:
            return self._infrastructure_client
        credential = actor.credential
        if credential is None:
            raise PermissionError("user_database_credential_required")

        try:
            authorization = await credential.authorization()
        except Exception as error:
            _log_credential_failure("user_token_read", error)
            raise PermissionError("user_database_credential_required") from None
        if authorization is None:
            raise PermissionError("user_database_credential_required")
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token or token != token.strip():
            raise PermissionError("invalid_user_database_credential")

        try:
            return self._user_client_factory(self._settings.workspace_host, token)
        except Exception as error:
            _log_credential_failure("user_workspace_client", error)
            raise PermissionError("user_database_credential_required") from None


async def _await_database_credential_mint(
    credential_api: PostgresCredentialApi, endpoint: str
) -> DatabaseCredential:
    """Keep caller cancellation pending until the SDK mint thread has exited."""
    mint = asyncio.create_task(
        asyncio.to_thread(
            credential_api.generate_database_credential,
            endpoint=endpoint,
        )
    )
    cancellation: asyncio.CancelledError | None = None
    current = asyncio.current_task()
    while not mint.done():
        try:
            await asyncio.shield(mint)
        except asyncio.CancelledError as error:
            if current is None or current.cancelling() == 0:
                raise
            if cancellation is None:
                cancellation = error
        except Exception:
            break

    if cancellation is not None:
        with suppress(BaseException):
            mint.result()
        raise cancellation
    return mint.result()


def _sdk_user_client(host: str, token: str, /) -> WorkspacePostgresClient:
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient(host=host, token=token, auth_type="pat")


def _log_credential_failure(phase: str, error: Exception) -> None:
    """Log only closed diagnostic categories, never SDK text or credential data."""
    from databricks.sdk import errors as sdk_errors

    exception_categories: tuple[tuple[type[BaseException], str], ...] = (
        (sdk_errors.Unauthenticated, "unauthenticated"),
        (sdk_errors.PermissionDenied, "permission_denied"),
        (sdk_errors.TooManyRequests, "rate_limited"),
        (sdk_errors.TemporarilyUnavailable, "temporarily_unavailable"),
        (sdk_errors.DeadlineExceeded, "deadline_exceeded"),
        (sdk_errors.BadRequest, "bad_request"),
        (sdk_errors.NotFound, "not_found"),
        (sdk_errors.InternalError, "internal_error"),
        (TimeoutError, "timeout"),
        (ConnectionError, "connection_error"),
        (PermissionError, "permission_error"),
    )
    exception_category = next(
        (label for error_type, label in exception_categories if isinstance(error, error_type)),
        "other",
    )

    raw_status = getattr(error, "status", None)
    if type(raw_status) is int and 100 <= raw_status <= 599:
        status = str(raw_status)
    elif (
        type(raw_status) is str
        and len(raw_status) == 3
        and raw_status.isascii()
        and raw_status.isdigit()
    ):
        status_number = int(raw_status)
        status = raw_status if 100 <= status_number <= 599 else "other"
    else:
        status = "none"

    safe_codes = {
        "PERMISSION_DENIED",
        "UNAUTHENTICATED",
        "INVALID_PARAMETER_VALUE",
        "RESOURCE_DOES_NOT_EXIST",
        "RESOURCE_EXHAUSTED",
        "TOO_MANY_REQUESTS",
        "INTERNAL_ERROR",
        "TEMPORARILY_UNAVAILABLE",
        "DEADLINE_EXCEEDED",
    }
    raw_code = getattr(error, "error_code", None)
    code = raw_code if type(raw_code) is str and raw_code in safe_codes else "other"
    _logger.warning(
        "tether_lakebase_credential_failure phase=%s exception=%s status=%s code=%s",
        phase,
        exception_category,
        status,
        code,
    )


def _connection_failure_kind(error: Exception) -> str:
    """Classify known connection failures without logging their message."""
    message = str(error).casefold()
    if any(marker in message for marker in ("certificate verify failed", "ssl certificate")):
        return "tls_verification"
    if any(
        marker in message
        for marker in (
            "could not translate host name",
            "name or service not known",
            "temporary failure in name resolution",
            "nodename nor servname",
        )
    ):
        return "dns_resolution"
    if any(marker in message for marker in ("connection timed out", "timeout expired")):
        return "connect_timeout"
    if any(
        marker in message
        for marker in (
            "password authentication failed",
            "role does not exist",
            "no pg_hba.conf entry",
            "authentication failed",
        )
    ):
        return "role_rejected"
    return "other"


__all__ = ["LakebaseConnectionSource", "LakebaseSettings", "app_service_workspace_client"]
