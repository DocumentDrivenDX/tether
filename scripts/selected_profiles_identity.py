"""Live caller separation and temporary identity revocation checks."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import httpx
import selected_profiles_identity_probe as _identity_probe
from databricks.sdk import WorkspaceClient
from selected_profiles_identity_admin import IdentityAdmin, WorkspaceIdentityAdmin
from selected_profiles_identity_credentials import FreshCredential
from selected_profiles_identity_probe import (
    fresh_workspace_access,
    wait_for_workspace_identity,
)
from selected_profiles_pg_oracle import probe_database
from selected_profiles_process import await_retained as _await_retained


def _authorization(workspace: WorkspaceClient) -> str:
    headers = dict(workspace.config.authenticate())
    value = headers.get("Authorization") or headers.get("authorization")
    if not isinstance(value, str) or not value.startswith("Bearer "):
        raise RuntimeError("selected_profile_credential_missing")
    return value


async def _authorization_async(workspace: WorkspaceClient) -> str:
    return await asyncio.to_thread(_authorization, workspace)


async def auth_refusal(origin: str, prefix: str) -> None:
    """Check absent and untrusted credentials before creating a session."""
    import uuid

    marker = "-".join(("fabricated", "untrusted", uuid.uuid4().hex))
    async with httpx.AsyncClient(base_url=origin, timeout=30, http2=True) as client:
        route = prefix + "/sessions"
        no_auth = await client.post(route, json={"request_id": "no-auth-" + uuid.uuid4().hex})
        invalid = await client.post(
            route,
            json={"request_id": "bad-auth-" + uuid.uuid4().hex},
            headers={"Authorization": " ".join(("Bearer", marker))},
        )
    if no_auth.status_code not in {401, 403} or invalid.status_code not in {401, 403}:
        raise RuntimeError("untrusted_request_not_refused")


async def _principal_status(
    origin: str,
    prefix: str,
    token: str,
    *,
    service: bool,
    timeout_seconds: float = 30,
) -> int:
    from tether.client.databricks import service_caller_auth

    async with httpx.AsyncClient(
        base_url=origin,
        timeout=timeout_seconds,
        http2=True,
        auth=service_caller_auth() if service else None,
    ) as client:
        response = await client.get(prefix + "/capabilities", headers={"Authorization": token})
    return response.status_code


async def revoke_restore_test_identity(
    *,
    admin_workspace: WorkspaceClient | None = None,
    identity_admin: IdentityAdmin | None = None,
    service_workspace: WorkspaceClient,
    identity_reference: Path,
    app_principal_id: str,
    app_name: str,
    origin: str,
    prefix: str,
    refresh_credential: Callable[[], Awaitable[FreshCredential]],
) -> dict[str, bool]:
    """@covers US-068-AC3: only the owned nonadmin identity is deactivated/restored."""
    from databricks.sdk.service.iam import PatchSchema

    reference = json.loads(await asyncio.to_thread(identity_reference.read_text, encoding="utf-8"))
    service_id = reference.get("id")
    application_id = reference.get("application_id")
    admin = _identity_admin(identity_admin, admin_workspace)
    service_id, application_id = await _validate_revocation_target(
        admin,
        service_workspace,
        service_id,
        application_id,
        app_principal_id,
        app_name,
    )
    active_token = await _authorization_async(service_workspace)
    schema = [PatchSchema.URN_IETF_PARAMS_SCIM_API_MESSAGES_2_0_PATCH_OP]
    operation = asyncio.create_task(
        _deactivate_check_and_restore(
            admin,
            service_workspace,
            service_id,
            application_id,
            active_token,
            origin,
            prefix,
            schema,
            refresh_credential,
        )
    )
    try:
        restored, denial_token_unexpired = await _await_retained(operation)
    except asyncio.CancelledError:
        raise
    restored, denial_token_unexpired = await operation
    if restored.active is not True or restored.application_id != application_id:
        raise RuntimeError("test_identity_restore_not_observed")
    return {"revocation_token_unexpired_at_denial": denial_token_unexpired}


async def _validate_revocation_target(
    admin_workspace: WorkspaceClient | IdentityAdmin,
    service_workspace: WorkspaceClient,
    service_id: object,
    application_id: object,
    app_principal_id: str,
    app_name: str,
) -> tuple[str, str]:
    admin_port = _resolve_admin(admin_workspace)
    identity, operator = await asyncio.gather(
        asyncio.to_thread(service_workspace.current_user.me),
        asyncio.to_thread(admin_port.operator),
    )
    if (
        not isinstance(service_id, str)
        or not isinstance(application_id, str)
        or not service_id
        or not application_id
        or identity.id != service_id
        or service_id == operator.id
        or application_id == app_principal_id
        or not app_name
    ):
        raise RuntimeError("revocation_target_not_owned_test_identity")
    if operator.account_admin and operator.workspace_id is None:
        raise RuntimeError("identity_admin_workspace_assignment_unavailable")
    target = await asyncio.to_thread(admin_port.get_service_principal, service_id)
    if (
        target.active is not True
        or target.id != service_id
        or target.application_id != application_id
    ):
        raise RuntimeError("revocation_target_identity_state_unexpected")
    return service_id, application_id


async def _restore_identity(
    admin_workspace: WorkspaceClient | IdentityAdmin,
    service_id: str,
    schemas: Any,
) -> Any:
    admin = _resolve_admin(admin_workspace)
    restored = None
    for _attempt in range(3):
        try:
            await asyncio.to_thread(admin.set_service_principal_active, service_id, True)
            candidate = await asyncio.to_thread(admin.get_service_principal, service_id)
        except Exception:
            candidate = None
        if candidate is not None and candidate.active is True:
            restored = candidate
            break
    if restored is None:
        raise RuntimeError("test_identity_restore_failed")
    return restored


async def _deactivate_check_and_restore(
    admin_workspace: WorkspaceClient | IdentityAdmin,
    service_workspace: WorkspaceClient,
    service_id: str,
    application_id: str,
    active_token: str,
    origin: str,
    prefix: str,
    schemas: Any,
    refresh_credential: Callable[[], Awaitable[FreshCredential]],
) -> tuple[Any, bool]:
    from selected_profiles_expiry import _token_expiry

    admin = _resolve_admin(admin_workspace)
    captured_token_expiry = _token_expiry(active_token)
    if captured_token_expiry <= int(time.time()):
        raise RuntimeError("revocation_token_expired_before_disable")
    denial_token_unexpired = False

    async def disable() -> None:
        await asyncio.to_thread(
            admin.set_service_principal_active,
            service_id,
            False,
        )

    async def check() -> None:
        nonlocal denial_token_unexpired
        deadline = time.monotonic() + _identity_probe._DEADLINE_SECONDS
        denial_token_unexpired = await _verify_revocation_denial(
            admin,
            service_workspace,
            service_id,
            application_id,
            active_token,
            captured_token_expiry,
            origin,
            prefix,
            deadline=deadline,
        )

    async def restore() -> Any:
        await _restore_identity(admin, service_id, schemas)
        return await _confirm_restored_identity(
            admin,
            refresh_credential,
            getattr(service_workspace.config, "host", None),
            service_id,
            application_id,
            origin,
            prefix,
        )

    restored = await _ordered_disable_check_restore(disable, check, restore)
    if restored.active is not True or restored.application_id != application_id:
        raise RuntimeError("test_identity_restore_not_observed")
    return restored, denial_token_unexpired


async def _verify_revocation_denial(
    admin: IdentityAdmin,
    service_workspace: WorkspaceClient,
    service_id: str,
    application_id: str,
    active_token: str,
    token_expiry: int,
    origin: str,
    prefix: str,
    *,
    deadline: float,
) -> bool:
    host = getattr(service_workspace.config, "host", None)
    if not isinstance(host, str):
        raise RuntimeError("identity_workspace_host_unavailable")

    try:
        async with asyncio.timeout_at(deadline):
            await _identity_probe.wait_for_identity_revocation(
                host,
                active_token,
                service_id,
                application_id,
                admin.get_service_principal,
                wait_for_workspace_identity,
                deadline=deadline,
            )
            if token_expiry <= int(time.time()):
                raise RuntimeError("revocation_token_expired_before_denial")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _identity_probe.IdentityObservationIncomplete(
                    "identity_workspace_transition_timeout"
                )
            status = await _principal_status(
                origin,
                prefix,
                active_token,
                service=True,
                timeout_seconds=min(30.0, remaining),
            )
            if status not in {401, 403}:
                raise RuntimeError("inactive_identity_still_authorized")
            if token_expiry <= int(time.time()):
                raise RuntimeError("revocation_token_expired_at_denial")
            return True
    except TimeoutError:
        raise _identity_probe.IdentityObservationIncomplete(
            "identity_workspace_transition_timeout"
        ) from None


async def _confirm_restored_identity(
    admin: IdentityAdmin,
    refresh_credential: Callable[[], Awaitable[FreshCredential]],
    trusted_host: object,
    service_id: str,
    application_id: str,
    origin: str,
    prefix: str,
) -> Any:
    if not isinstance(trusted_host, str):
        raise RuntimeError("identity_workspace_host_unavailable")

    def protected_status_for(token: str) -> Callable[[], Any]:
        async def protected_status() -> int:
            return await _principal_status(
                origin,
                prefix,
                token,
                service=True,
                timeout_seconds=10,
            )

        return protected_status

    await fresh_workspace_access(
        refresh_credential,
        trusted_host,
        service_id,
        protected_status_for,
    )
    confirmed = await asyncio.to_thread(admin.get_service_principal, service_id)
    if (
        confirmed.id != service_id
        or confirmed.application_id != application_id
        or confirmed.active is not True
    ):
        raise RuntimeError("test_identity_restore_not_observed")
    return confirmed


async def _ordered_disable_check_restore(disable: Any, check: Any, restore: Any) -> Any:
    try:
        await disable()
        await check()
    except BaseException:
        await restore()
        raise
    else:
        return await restore()


async def revocation_and_refusal(
    origin: str,
    prefix: str,
    *,
    admin_workspace: WorkspaceClient | None = None,
    identity_admin: IdentityAdmin | None = None,
    service_workspace: WorkspaceClient,
    identity_reference: Path,
    app_principal_id: str,
    app_name: str,
    refresh_credential: Callable[[], Awaitable[FreshCredential]],
) -> dict[str, bool]:
    """@covers US-068-AC3: absent, invalid, revoked and restored credentials behave safely."""
    await auth_refusal(origin, prefix)
    return await revoke_restore_test_identity(
        admin_workspace=admin_workspace,
        identity_admin=identity_admin,
        service_workspace=service_workspace,
        identity_reference=identity_reference,
        app_principal_id=app_principal_id,
        app_name=app_name,
        origin=origin,
        prefix=prefix,
        refresh_credential=refresh_credential,
    )


def _identity_admin(
    identity_admin: IdentityAdmin | None, workspace: WorkspaceClient | None
) -> IdentityAdmin:
    if identity_admin is not None:
        return identity_admin
    if workspace is None:
        raise RuntimeError("identity_admin_unavailable")
    return WorkspaceIdentityAdmin(workspace)


def _resolve_admin(value: WorkspaceClient | IdentityAdmin) -> IdentityAdmin:
    if _is_identity_admin(value):
        return cast(IdentityAdmin, value)
    return WorkspaceIdentityAdmin(cast(WorkspaceClient, value))


def _is_identity_admin(value: WorkspaceClient | IdentityAdmin) -> bool:
    return (
        callable(getattr(value, "operator", None))
        and callable(getattr(value, "get_service_principal", None))
        and callable(getattr(value, "set_service_principal_active", None))
    )


async def principal_separation(
    human_workspace: WorkspaceClient,
    service_workspace: WorkspaceClient,
    app_principal_id: str,
    infra_database_role: str,
    origin: str,
    prefix: str,
    settings: Any,
    service_database_role: str,
) -> dict[str, str]:
    """@covers US-068-AC4: callers and caller DB credentials cannot use app identity."""
    human, service = await asyncio.gather(
        asyncio.to_thread(human_workspace.current_user.me),
        asyncio.to_thread(service_workspace.current_user.me),
    )
    app = app_principal_id.strip()
    if (
        not app
        or app != infra_database_role
        or app in {human.id, human.user_name, service.id, service.user_name}
    ):
        raise RuntimeError("app_and_caller_principals_not_separate")
    if human.id == service.id or human.user_name == service.user_name:
        raise RuntimeError("selected_callers_not_distinct")
    human_token, service_token = await asyncio.gather(
        _authorization_async(human_workspace), _authorization_async(service_workspace)
    )
    if await _principal_status(origin, prefix, human_token, service=False) != 200:
        raise RuntimeError("selected_caller_route_unavailable")
    if await _principal_status(origin, prefix, service_token, service=True) != 200:
        raise RuntimeError("selected_caller_route_unavailable")
    refusal_state, driver_version = await _wrong_role_database_refusal(
        service_workspace, settings, service_database_role, infra_database_role
    )
    return {
        "database_auth_refusal_sqlstate": refusal_state,
        "database_oracle_driver_version": driver_version,
    }


async def _wrong_role_database_refusal(
    workspace: WorkspaceClient,
    settings: Any,
    caller_role: str,
    infrastructure_role: str,
) -> tuple[str, str]:
    import asyncio

    if not caller_role or not infrastructure_role or caller_role == infrastructure_role:
        raise RuntimeError("database_principal_roles_not_separate")
    credential = await asyncio.to_thread(
        workspace.postgres.generate_database_credential, endpoint=settings.pg_endpoint
    )
    token = getattr(credential, "token", None)
    if not isinstance(token, str) or not token:
        raise RuntimeError("service_database_credential_unavailable")
    connection = {
        "host": settings.pg_host,
        "port": int(getattr(settings, "pg_port", 5432)),
        "password": token,
        "database": settings.pg_database,
        "ca_file": settings.ca_file,
    }
    caller = await probe_database(**connection, user=caller_role)
    if caller.kind != "connected" or caller.current_user != caller_role:
        raise RuntimeError("caller_database_role_oracle_unavailable")
    refused = await probe_database(**connection, user=infrastructure_role)
    if refused.kind == "connected":
        raise RuntimeError("caller_token_accepted_as_infrastructure_role")
    if refused.kind != "auth_refused" or refused.sqlstate not in {"28000", "28P01"}:
        raise RuntimeError("wrong_role_database_refusal_unclassified")
    if caller.driver_version != refused.driver_version:
        raise RuntimeError("database_oracle_driver_version_mismatch")
    return refused.sqlstate, refused.driver_version


__all__ = ["principal_separation", "revocation_and_refusal"]
