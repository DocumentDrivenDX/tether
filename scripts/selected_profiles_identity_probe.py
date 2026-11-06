"""Bounded workspace-authoritative convergence checks for identity revocation."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import httpx
from selected_profiles_identity_credentials import CredentialRefreshError, FreshCredential

_ME_PATH = "/api/2.0/preview/scim/v2/Me"
_DEADLINE_SECONDS = 90.0
_REQUEST_TIMEOUT_SECONDS = 10.0
_POLL_INTERVAL_SECONDS = 2.0
_RESTORE_AUTH_MIN_REMAINING_SECONDS = 30.0


class IdentityObservationIncomplete(RuntimeError):
    """The bounded live identity observation ended without proving a state."""

    _ALLOWED_REASONS = frozenset({"identity_workspace_transition_timeout"})

    def __init__(self, reason: str) -> None:
        self.reason = (
            reason if reason in self._ALLOWED_REASONS else "identity_observation_incomplete"
        )
        super().__init__(self.reason)


def _client(host: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=host,
        timeout=httpx.Timeout(_REQUEST_TIMEOUT_SECONDS),
        trust_env=False,
        verify=True,
    )


def _validate_inputs(host: str, authorization: str, principal_id: str) -> str:
    try:
        parsed = urlsplit(host)
    except ValueError:
        raise RuntimeError("identity_workspace_host_invalid") from None
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("identity_workspace_host_invalid")
    if (
        not authorization.startswith("Bearer ")
        or not authorization.removeprefix("Bearer ").strip()
        or "\r" in authorization
        or "\n" in authorization
    ):
        raise RuntimeError("identity_workspace_credential_invalid")
    if (
        not principal_id
        or not principal_id.isascii()
        or "\r" in principal_id
        or "\n" in principal_id
    ):
        raise RuntimeError("identity_workspace_principal_invalid")
    return f"{parsed.scheme}://{parsed.netloc}"


async def wait_for_workspace_identity(
    host: str,
    authorization: str,
    principal_id: str,
    *,
    expected_active: bool,
    deadline: float | None = None,
) -> str:
    """Wait for a fresh same-bearer `/Me` response to converge to the expected state."""
    origin = _validate_inputs(host, authorization, principal_id)
    deadline = _resolve_deadline(deadline)
    budget = _remaining(deadline)
    try:
        async with asyncio.timeout(budget):
            async with _client(origin) as client:
                while True:
                    try:
                        response = await client.get(
                            _ME_PATH,
                            headers={"Authorization": authorization, "Accept": "application/json"},
                        )
                    except (httpx.TimeoutException, httpx.TransportError):
                        response = None
                    outcome = _classify_response(response, principal_id, expected_active)
                    if outcome is not None:
                        return outcome

                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise IdentityObservationIncomplete("identity_workspace_transition_timeout")
                    await asyncio.sleep(min(_POLL_INTERVAL_SECONDS, remaining))
    except TimeoutError:
        raise IdentityObservationIncomplete("identity_workspace_transition_timeout") from None


async def wait_for_identity_revocation(
    host: str,
    authorization: str,
    principal_id: str,
    application_id: str,
    account_lookup: Callable[[str], object],
    workspace_check: Callable[..., Awaitable[str]],
    *,
    deadline: float,
) -> None:
    """Require workspace and account revocation to converge within one deadline."""

    async def wait_for_account_inactive() -> None:
        while True:
            remaining = _remaining(deadline)
            if remaining <= 0:
                raise IdentityObservationIncomplete("identity_workspace_transition_timeout")
            record: Any = await asyncio.to_thread(account_lookup, principal_id)
            if (
                getattr(record, "id", None) != principal_id
                or getattr(record, "application_id", None) != application_id
                or type(getattr(record, "active", None)) is not bool
            ):
                raise RuntimeError("test_identity_account_state_unverified")
            if record.active is False:
                return
            await asyncio.sleep(min(_POLL_INTERVAL_SECONDS, _remaining(deadline)))

    async def wait_for_workspace_inactive() -> None:
        await workspace_check(
            host,
            authorization,
            principal_id,
            expected_active=False,
            deadline=deadline,
        )

    _validate_inputs(host, authorization, principal_id)
    try:
        async with asyncio.timeout(_remaining(_resolve_deadline(deadline))):
            workspace_task = asyncio.create_task(wait_for_workspace_inactive())
            account_wait = asyncio.create_task(wait_for_account_inactive())
            try:
                await asyncio.gather(workspace_task, account_wait)
            finally:
                pending = [task for task in (workspace_task, account_wait) if not task.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
            await wait_for_workspace_inactive()
    except TimeoutError:
        raise IdentityObservationIncomplete("identity_workspace_transition_timeout") from None


async def wait_for_workspace_access(
    host: str,
    authorization: str,
    principal_id: str,
    protected_status: Callable[[], Awaitable[int]],
    *,
    deadline: float | None = None,
) -> str:
    """Wait for the fresh identity and its protected read route under one deadline."""
    origin = _validate_inputs(host, authorization, principal_id)
    deadline = _resolve_deadline(deadline)
    budget = _remaining(deadline)
    try:
        async with asyncio.timeout(budget):
            async with _client(origin) as client:
                while True:
                    if await _access_is_ready(
                        client, authorization, principal_id, protected_status
                    ):
                        return "active"
                    await _pause_until(deadline)
    except TimeoutError:
        raise IdentityObservationIncomplete("identity_workspace_transition_timeout") from None


async def fresh_workspace_access(
    refresh_credential: Callable[[], Awaitable[FreshCredential]],
    trusted_host: str,
    principal_id: str,
    protected_status_for: Callable[[str], Callable[[], Awaitable[int]]],
) -> FreshCredential:
    """Acquire fresh credentials and prove matching workspace access under one deadline."""
    deadline = time.monotonic() + _DEADLINE_SECONDS
    credential: FreshCredential | None = None
    try:
        async with asyncio.timeout_at(deadline):
            while credential is None:
                if _remaining(deadline) <= _RESTORE_AUTH_MIN_REMAINING_SECONDS:
                    raise IdentityObservationIncomplete("identity_workspace_transition_timeout")
                try:
                    credential = await refresh_credential()
                    if credential.host != trusted_host:
                        raise RuntimeError("restored_workspace_host_mismatch")
                    _validate_inputs(credential.host, credential.authorization, principal_id)
                except CredentialRefreshError as error:
                    if not error.retryable:
                        raise
                    credential = None
                    await _pause_until(deadline)
            await wait_for_workspace_access(
                trusted_host,
                credential.authorization,
                principal_id,
                protected_status_for(credential.authorization),
                deadline=deadline,
            )
            return credential
    except TimeoutError:
        raise IdentityObservationIncomplete("identity_workspace_transition_timeout") from None


async def _access_is_ready(
    client: httpx.AsyncClient,
    authorization: str,
    principal_id: str,
    protected_status: Callable[[], Awaitable[int]],
) -> bool:
    try:
        response = await client.get(
            _ME_PATH,
            headers={"Authorization": authorization, "Accept": "application/json"},
        )
    except (httpx.TimeoutException, httpx.TransportError):
        response = None
    if _classify_response(response, principal_id, True) != "active":
        return False
    try:
        status = await protected_status()
    except (httpx.TimeoutException, httpx.TransportError):
        status = None
    if status == 200:
        return True
    if status not in {None, 401, 403, 429, 500, 502, 503, 504}:
        raise RuntimeError("identity_workspace_route_unclassified")
    return False


async def _pause_until(deadline: float) -> None:
    remaining = _remaining(deadline)
    if remaining <= 0:
        raise IdentityObservationIncomplete("identity_workspace_transition_timeout")
    await asyncio.sleep(min(_POLL_INTERVAL_SECONDS, remaining))


def _resolve_deadline(deadline: float | None) -> float:
    resolved = deadline if deadline is not None else time.monotonic() + _DEADLINE_SECONDS
    remaining = _remaining(resolved)
    if remaining <= 0:
        raise IdentityObservationIncomplete("identity_workspace_transition_timeout")
    return resolved


def _remaining(deadline: float) -> float:
    return deadline - time.monotonic()


def _classify_response(
    response: httpx.Response | None, principal_id: str, expected_active: bool
) -> str | None:
    if response is None:
        return None
    if response.status_code in {401, 403}:
        return "refused" if not expected_active else None
    if response.status_code != 200:
        if response.status_code in {429, 500, 502, 503, 504}:
            return None
        raise RuntimeError("identity_workspace_response_unclassified")
    try:
        value = response.json()
    except ValueError:
        raise RuntimeError("identity_workspace_response_invalid") from None
    if not isinstance(value, dict):
        raise RuntimeError("identity_workspace_response_invalid")
    if value.get("id") != principal_id:
        raise RuntimeError("identity_workspace_principal_mismatch")
    active = value.get("active")
    if type(active) is bool and active is expected_active:
        return "active" if expected_active else "inactive"
    return None


__all__ = [
    "IdentityObservationIncomplete",
    "wait_for_workspace_access",
    "wait_for_workspace_identity",
]
