"""Natural OAuth expiry and fresh database-credential probes."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from databricks.sdk import WorkspaceClient
from psycopg import AsyncConnection
from psycopg.conninfo import make_conninfo
from selected_profiles_identity_credentials import FreshCredential, workspace_from_credential
from selected_profiles_pg_oracle import OracleResult, probe_database


def _token_expiry(authorization: str) -> int:
    token = authorization.split(" ", maxsplit=1)[1]
    pieces = token.split(".")
    if len(pieces) != 3:
        raise RuntimeError("oauth_token_expiry_unavailable")
    payload = pieces[1] + "=" * (-len(pieces[1]) % 4)
    value = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    expiry = value.get("exp")
    if isinstance(expiry, bool) or not isinstance(expiry, int):
        raise RuntimeError("oauth_token_expiry_unavailable")
    return expiry


async def _database_credential(workspace: WorkspaceClient, endpoint: str) -> tuple[str, int]:
    credential = await asyncio.to_thread(
        workspace.postgres.generate_database_credential, endpoint=endpoint
    )
    if not credential.token or credential.expire_time is None:
        raise RuntimeError("database_credential_expiry_unavailable")
    return credential.token, credential.expire_time.seconds


async def _open_database(settings: Any, username: str, password: str) -> None:
    connection = await AsyncConnection.connect(
        make_conninfo(
            host=settings.pg_host,
            dbname=settings.pg_database,
            user=username,
            password=password,
            sslmode="verify-full",
            sslrootcert=settings.ca_file,
            connect_timeout=25,
        )
    )
    await connection.close()


def _is_expired_database_credential_error(result: OracleResult) -> bool:
    """Accept only the isolated driver's typed PostgreSQL authentication result."""
    return result.kind == "auth_refused" and result.sqlstate in {"28000", "28P01"}


async def _protected_route(origin: str, prefix: str, token: str) -> int:
    from tether.client.databricks import service_caller_auth

    headers = {"Authorization": token}
    async with httpx.AsyncClient(
        base_url=origin,
        timeout=25,
        http2=True,
        auth=service_caller_auth(),
        follow_redirects=False,
    ) as client:
        response = await client.get(prefix + "/capabilities", headers=headers)
    return response.status_code


async def _after_expiry(
    workspace: WorkspaceClient,
    settings: Any,
    origin: str,
    prefix: str,
    token: str,
    database_token: str,
    expiry: int,
    username: str,
    refresh_credential: Callable[[], Awaitable[FreshCredential]],
) -> tuple[int, int, str, str]:
    delay = max(0.0, expiry + 6 - time.time())
    loop = asyncio.get_running_loop()
    elapsed = loop.create_future()
    timer = loop.call_later(delay, elapsed.set_result, None)
    try:
        await elapsed
    finally:
        timer.cancel()
    observed_at = int(time.time())
    stale_status = await _protected_route(origin, prefix, token)
    credential = await refresh_credential()
    refreshed = workspace_from_credential(credential)
    identity = await asyncio.to_thread(refreshed.current_user.me)
    if getattr(identity, "user_name", None) != username:
        raise RuntimeError("fresh_database_caller_identity_changed")
    if await _protected_route(origin, prefix, credential.authorization) != 200:
        raise RuntimeError("fresh_oauth_route_failed")
    fresh_database, _fresh_expiry = await _database_credential(refreshed, settings.pg_endpoint)
    await _open_database(settings, username, fresh_database)
    stale_result = await probe_database(
        host=settings.pg_host,
        port=int(getattr(settings, "pg_port", 5432)),
        user=username,
        password=database_token,
        database=settings.pg_database,
        ca_file=settings.ca_file,
    )
    stale_database_rejected = _is_expired_database_credential_error(stale_result)
    if stale_status not in {401, 403} or not stale_database_rejected:
        raise RuntimeError("expired_credential_still_accepted")
    return observed_at, stale_status, stale_result.sqlstate or "", stale_result.driver_version


async def credential_rotation(
    workspace: WorkspaceClient,
    settings: Any,
    origin: str,
    prefix: str,
    *,
    refresh_credential: Callable[[], Awaitable[FreshCredential]],
) -> dict[str, int | str]:
    """@covers US-068-AC3 @covers US-068-AC6: expired tokens fail before refresh."""
    identity = await asyncio.to_thread(workspace.current_user.me)
    username = identity.user_name
    if not isinstance(username, str) or not username:
        raise RuntimeError("database_caller_identity_unavailable")
    token = await asyncio.to_thread(
        lambda: dict(workspace.config.authenticate()).get("Authorization")
    )
    if not isinstance(token, str) or not token.startswith("Bearer "):
        raise RuntimeError("selected_profile_credential_missing")
    database_token, database_expiry = await _database_credential(workspace, settings.pg_endpoint)
    minted_at = int(time.time())
    oauth_expiry = _token_expiry(token)
    if oauth_expiry <= minted_at or database_expiry <= minted_at:
        raise RuntimeError("credential_minted_expired")
    if max(oauth_expiry, database_expiry) - minted_at > 9 * 60 * 60:
        raise RuntimeError("credential_expiry_outside_bounded_window")
    if await _protected_route(origin, prefix, token) != 200:
        raise RuntimeError("fresh_oauth_route_failed")
    await _open_database(settings, username, database_token)
    expiry = max(oauth_expiry, database_expiry)
    (
        observed_at,
        _stale_status,
        database_refusal_sqlstate,
        database_oracle_driver_version,
    ) = await _after_expiry(
        workspace,
        settings,
        origin,
        prefix,
        token,
        database_token,
        expiry,
        username,
        refresh_credential,
    )
    fresh_credential = await refresh_credential()
    refreshed = workspace_from_credential(fresh_credential)
    fresh_identity = await asyncio.to_thread(refreshed.current_user.me)
    if getattr(fresh_identity, "user_name", None) != username:
        raise RuntimeError("fresh_database_caller_identity_changed")
    fresh_database, _ = await _database_credential(refreshed, settings.pg_endpoint)
    fresh_status = await _protected_route(origin, prefix, fresh_credential.authorization)
    if fresh_status != 200:
        raise RuntimeError("fresh_oauth_route_failed")
    await _open_database(settings, username, fresh_database)
    if observed_at <= max(oauth_expiry, database_expiry):
        raise RuntimeError("credential_expiry_not_genuinely_elapsed")
    return {
        "minted_at_epoch_seconds": minted_at,
        "oauth_expiry_epoch_seconds": oauth_expiry,
        "database_expiry_epoch_seconds": database_expiry,
        "observed_at_epoch_seconds": observed_at,
        "expired_credential_rejected": 1,
        "fresh_credentials_accepted": 1,
        "database_credential_refusal_sqlstate": database_refusal_sqlstate,
        "database_oracle_driver_version": database_oracle_driver_version,
    }


__all__ = ["credential_rotation"]
