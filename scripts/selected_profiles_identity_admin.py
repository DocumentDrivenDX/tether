"""Explicit account-SCIM identity administration for selected-profile checks."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import quote, urlsplit

import httpx
from databricks.sdk import AccountClient, WorkspaceClient
from databricks.sdk.config import Config
from selected_profiles_logs import _clean_environment

_ACCOUNT_HOST = "https://accounts.azuredatabricks.net"
_PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
_REFERENCE_KEYS = {
    "schema_version",
    "mode",
    "account_host",
    "account_id",
    "operator_account_user_id",
    "workspace_id",
}


@dataclass(frozen=True, slots=True)
class IdentityOperator:
    id: str
    user_name: str | None
    account_admin: bool
    workspace_id: int | None


@dataclass(frozen=True, slots=True)
class ManagedServicePrincipal:
    id: str
    application_id: str
    active: bool


class IdentityAdmin(Protocol):
    """Narrow typed surface used by the revocation controller."""

    def operator(self) -> IdentityOperator: ...

    def get_service_principal(self, principal_id: str) -> ManagedServicePrincipal: ...

    def set_service_principal_active(self, principal_id: str, active: bool) -> None: ...


class WorkspaceIdentityAdmin:
    """Compatibility adapter over the existing workspace SCIM client."""

    def __init__(self, workspace: WorkspaceClient) -> None:
        self._workspace = workspace

    def operator(self) -> IdentityOperator:
        current = self._workspace.current_user.me()
        return IdentityOperator(
            id=str(current.id or ""),
            user_name=getattr(current, "user_name", None),
            account_admin=False,
            workspace_id=None,
        )

    def get_service_principal(self, principal_id: str) -> ManagedServicePrincipal:
        value = self._workspace.service_principals.get(principal_id)
        identity = ManagedServicePrincipal(
            id=_required_string(value, "id", "identity_admin_service_principal_invalid"),
            application_id=_required_string(
                value, "application_id", "identity_admin_service_principal_invalid"
            ),
            active=_required_boolean(value, "active", "identity_admin_service_principal_invalid"),
        )
        if identity.id != principal_id:
            raise RuntimeError("identity_admin_service_principal_invalid")
        return identity

    def set_service_principal_active(self, principal_id: str, active: bool) -> None:
        from databricks.sdk.service.iam import Patch, PatchOp, PatchSchema

        self._workspace.service_principals.patch(
            principal_id,
            operations=[Patch(op=PatchOp.REPLACE, value={"active": active})],
            schemas=[PatchSchema.URN_IETF_PARAMS_SCIM_API_MESSAGES_2_0_PATCH_OP],
        )


class AccountScimIdentityAdmin:
    """Account-SCIM 2.1 adapter pinned to a validated account and target scope."""

    def __init__(
        self,
        account: AccountClient,
        *,
        account_id: str,
        account_host: str,
        operator: IdentityOperator,
    ) -> None:
        self._account = account
        self._account_id = account_id
        self._account_host = account_host
        self._operator = operator

    def operator(self) -> IdentityOperator:
        return self._operator

    def get_service_principal(self, principal_id: str) -> ManagedServicePrincipal:
        value = self._get_scim("ServicePrincipals", principal_id)
        identity = ManagedServicePrincipal(
            id=_required_string(value, "id", "identity_admin_service_principal_invalid"),
            application_id=_required_string(
                value, "applicationId", "identity_admin_service_principal_invalid"
            ),
            active=_required_boolean(value, "active", "identity_admin_service_principal_invalid"),
        )
        if identity.id != principal_id:
            raise RuntimeError("identity_admin_service_principal_invalid")
        return identity

    def set_service_principal_active(self, principal_id: str, active: bool) -> None:
        path = self._scim_path("ServicePrincipals", principal_id)
        body = {
            "schemas": [_PATCH_SCHEMA],
            "Operations": [
                {
                    "op": "replace",
                    "path": "active",
                    "value": [{"value": "true" if active else "false"}],
                }
            ],
        }
        response = self._request("PATCH", path, json=body)
        if response.status_code not in {200, 204}:
            raise RuntimeError("identity_admin_patch_refused")

    def _get_scim(self, resource: str, resource_id: str) -> Mapping[str, Any]:
        response = self._request("GET", self._scim_path(resource, resource_id))
        if response.status_code != 200:
            raise RuntimeError("identity_admin_lookup_refused")
        value = response.json()
        if not isinstance(value, Mapping):
            raise RuntimeError("identity_admin_response_invalid")
        return cast(Mapping[str, Any], value)

    def _scim_path(self, resource: str, resource_id: str) -> str:
        if resource not in {"Users", "ServicePrincipals"} or not resource_id:
            raise RuntimeError("identity_admin_target_invalid")
        return (
            f"/api/2.1/accounts/{quote(self._account_id, safe='')}"
            f"/scim/v2/{resource}/{quote(resource_id, safe='')}"
        )

    def _request(
        self, method: str, path: str, *, json: Mapping[str, Any] | None = None
    ) -> httpx.Response:
        return _scim_request(self._account, self._account_host, method, path, json=json)


async def identity_admin_from_reference(
    reference: Path,
    *,
    primary_workspace: WorkspaceClient,
    identity_reference: Path,
    app_principal_id: str,
) -> IdentityAdmin:
    """Validate an existing Azure CLI account-admin identity without granting access."""
    record, target = await asyncio.gather(
        asyncio.to_thread(_read_reference, reference),
        asyncio.to_thread(_read_target, identity_reference),
    )
    account_host, account_id, operator_id, workspace_id = record
    target_id, target_application_id = target
    if not app_principal_id or target_application_id == app_principal_id:
        raise RuntimeError("identity_admin_target_not_isolated")

    account = await asyncio.to_thread(_account_client, account_host, account_id)
    cli_username, primary = await asyncio.gather(
        asyncio.to_thread(_azure_cli_username),
        asyncio.to_thread(primary_workspace.current_user.me),
    )
    primary_username = _normalize_username(primary.user_name)
    if primary_username is None or primary_username != _normalize_username(cli_username):
        raise RuntimeError("identity_admin_caller_mismatch")
    account_user, target_record = await asyncio.gather(
        asyncio.to_thread(_account_user, account, operator_id),
        asyncio.to_thread(_account_service_principal, account, target_id),
    )
    await asyncio.to_thread(
        _verify_target_workspace_binding,
        account,
        account_id,
        workspace_id,
        target_id,
        primary_workspace.get_workspace_id,
    )
    if (
        account_user[0] != operator_id
        or not account_user[1]
        or _normalize_username(account_user[1]) != primary_username
        or not account_user[2]
        or not account_user[3]
        or target_record.id != target_id
        or target_record.application_id != target_application_id
        or not target_record.active
        or target_id == primary.id
        or target_application_id == app_principal_id
    ):
        raise RuntimeError("identity_admin_scope_not_verified")
    operator = IdentityOperator(
        id=operator_id,
        user_name=account_user[1],
        account_admin=True,
        workspace_id=workspace_id,
    )
    return AccountScimIdentityAdmin(
        account,
        account_id=account_id,
        account_host=account_host,
        operator=operator,
    )


def _read_reference(path: Path) -> tuple[str, str, str, int]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise RuntimeError("identity_admin_reference_invalid") from None
    if not isinstance(value, dict) or set(value) != _REFERENCE_KEYS:
        raise RuntimeError("identity_admin_reference_invalid")
    host = value.get("account_host")
    account_id = value.get("account_id")
    operator_id = value.get("operator_account_user_id")
    workspace_id = value.get("workspace_id")
    parsed = urlsplit(host) if isinstance(host, str) else None
    if (
        type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("mode") != "account-scim-azure-cli"
        or parsed is None
        or parsed.scheme != "https"
        or parsed.netloc.lower() != "accounts.azuredatabricks.net"
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or not isinstance(account_id, str)
        or not account_id
        or "/" in account_id
        or not isinstance(operator_id, str)
        or not operator_id.isascii()
        or not operator_id.isdecimal()
        or int(operator_id) <= 0
        or not isinstance(workspace_id, int)
        or isinstance(workspace_id, bool)
        or workspace_id <= 0
    ):
        raise RuntimeError("identity_admin_reference_invalid")
    return _ACCOUNT_HOST, account_id, operator_id, workspace_id


def _account_client(account_host: str, account_id: str) -> AccountClient:
    config = Config(
        host=account_host,
        account_id=account_id,
        auth_type="azure-cli",
        http_timeout_seconds=15,
        retry_timeout_seconds=10,
    )
    return AccountClient(config=config)


def _read_target(path: Path) -> tuple[str, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise RuntimeError("identity_admin_target_invalid") from None
    if not isinstance(value, dict):
        raise RuntimeError("identity_admin_target_invalid")
    principal_id = value.get("id")
    application_id = value.get("application_id")
    if not isinstance(principal_id, str) or not principal_id:
        raise RuntimeError("identity_admin_target_invalid")
    if not isinstance(application_id, str) or not application_id:
        raise RuntimeError("identity_admin_target_invalid")
    return principal_id, application_id


def _azure_cli_username() -> str:
    executable = shutil.which("az")
    if executable is None:
        raise RuntimeError("identity_admin_azure_cli_unavailable")
    try:
        result = subprocess.run(  # noqa: S603 - fixed az identity query, executable resolved from PATH
            [
                *_clean_environment(str(Path.home())),
                executable,
                "account",
                "show",
                "--query",
                "user.name",
                "--output",
                "tsv",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("identity_admin_azure_cli_unavailable") from None
    username = result.stdout.strip()
    if not username or "\n" in username:
        raise RuntimeError("identity_admin_azure_cli_identity_unavailable")
    return username


def _account_user(account: AccountClient, user_id: str) -> tuple[str, str | None, bool, bool]:
    value = _scim_get(account, "Users", user_id)
    roles = value.get("roles")
    account_admin = isinstance(roles, Sequence) and any(
        isinstance(role, Mapping)
        and isinstance(role.get("value"), str)
        and role["value"].casefold() == "account_admin"
        for role in roles
    )
    return (
        _required_string(value, "id", "identity_admin_user_invalid"),
        value.get("userName") if isinstance(value.get("userName"), str) else None,
        account_admin,
        value.get("active") is True,
    )


def _account_service_principal(
    account: AccountClient, principal_id: str
) -> ManagedServicePrincipal:
    value = _scim_get(account, "ServicePrincipals", principal_id)
    identity = ManagedServicePrincipal(
        id=_required_string(value, "id", "identity_admin_service_principal_invalid"),
        application_id=_required_string(
            value, "applicationId", "identity_admin_service_principal_invalid"
        ),
        active=_required_boolean(value, "active", "identity_admin_service_principal_invalid"),
    )
    if identity.id != principal_id:
        raise RuntimeError("identity_admin_service_principal_invalid")
    return identity


def _verify_target_workspace_binding(
    account: AccountClient,
    account_id: str,
    expected_workspace_id: int,
    target_id: str,
    get_primary_workspace_id: Callable[[], int],
) -> None:
    if get_primary_workspace_id() != expected_workspace_id:
        raise RuntimeError("identity_admin_workspace_host_mismatch")
    workspace = account.workspaces.get(expected_workspace_id)
    if (
        getattr(workspace, "workspace_id", None) != expected_workspace_id
        or getattr(workspace, "account_id", None) != account_id
    ):
        raise RuntimeError("identity_admin_workspace_record_mismatch")

    target_numeric_id = int(target_id) if target_id.isascii() and target_id.isdecimal() else 0
    if target_numeric_id <= 0:
        raise RuntimeError("identity_admin_target_invalid")
    workspace_ids = _listed_workspace_ids(account)
    if expected_workspace_id not in workspace_ids:
        raise RuntimeError("identity_admin_workspace_list_invalid")
    assigned_workspace_ids = _target_assignment_ids(account, workspace_ids, target_numeric_id)
    if assigned_workspace_ids != [expected_workspace_id]:
        raise RuntimeError("identity_admin_target_workspace_assignment_mismatch")


def _listed_workspace_ids(account: AccountClient) -> tuple[int, ...]:
    records = tuple(islice(account.workspaces.list(), 1001))
    if len(records) > 1000:
        raise RuntimeError("identity_admin_workspace_list_exceeds_bound")
    ids = tuple(
        workspace_id
        for record in records
        if isinstance((workspace_id := getattr(record, "workspace_id", None)), int)
        and not isinstance(workspace_id, bool)
    )
    if len(ids) != len(records) or len(set(ids)) != len(ids):
        raise RuntimeError("identity_admin_workspace_list_invalid")
    return ids


def _target_assignment_ids(
    account: AccountClient, workspace_ids: tuple[int, ...], target_id: int
) -> list[int]:
    found: list[int] = []
    for workspace_id in workspace_ids:
        assignments = tuple(islice(account.workspace_assignment.list(workspace_id), 1001))
        if len(assignments) > 1000:
            raise RuntimeError("identity_admin_assignment_list_exceeds_bound")
        found.extend(
            workspace_id
            for assignment in assignments
            if getattr(getattr(assignment, "principal", None), "principal_id", None) == target_id
            and _require_user_workspace_permission(assignment)
        )
    return found


def _require_user_workspace_permission(assignment: Any) -> bool:
    permissions = getattr(assignment, "permissions", None)
    normalized = {
        str(getattr(permission, "value", permission)).upper() for permission in permissions or ()
    }
    if normalized != {"USER"}:
        raise RuntimeError("identity_admin_target_workspace_permission_invalid")
    return True


def _scim_get(account: AccountClient, resource: str, resource_id: str) -> Mapping[str, Any]:
    if resource not in {"Users", "ServicePrincipals"} or not resource_id:
        raise RuntimeError("identity_admin_target_invalid")
    account_id = account.config.account_id
    account_host = account.config.host
    if not isinstance(account_id, str) or not isinstance(account_host, str):
        raise RuntimeError("identity_admin_reference_invalid")
    response = _scim_request(
        account,
        account_host,
        "GET",
        "/api/2.1/accounts/"
        f"{quote(account_id, safe='')}/scim/v2/{resource}/{quote(resource_id, safe='')}",
    )
    if response.status_code != 200:
        raise RuntimeError("identity_admin_lookup_refused")
    value = response.json()
    if not isinstance(value, Mapping):
        raise RuntimeError("identity_admin_response_invalid")
    return cast(Mapping[str, Any], value)


def _scim_request(
    account: AccountClient,
    account_host: str,
    method: str,
    path: str,
    *,
    json: Mapping[str, Any] | None = None,
) -> httpx.Response:
    headers = dict(account.config.authenticate())
    authorization = headers.get("Authorization") or headers.get("authorization")
    if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
        raise RuntimeError("identity_admin_credential_unavailable")
    with httpx.Client(timeout=20, trust_env=False, verify=True) as client:
        return client.request(
            method,
            account_host.rstrip("/") + path,
            headers={
                "Authorization": authorization,
                "Accept": "application/scim+json",
                "Content-Type": "application/scim+json",
            },
            json=json,
        )


def _normalize_username(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().casefold()


def _required_string(value: Any, key: str, error_code: str) -> str:
    field = value.get(key) if isinstance(value, Mapping) else getattr(value, key, None)
    if not isinstance(field, str) or not field:
        raise RuntimeError(error_code)
    return field


def _required_boolean(value: Any, key: str, error_code: str) -> bool:
    field = value.get(key) if isinstance(value, Mapping) else getattr(value, key, None)
    if type(field) is not bool:
        raise RuntimeError(error_code)
    return field


__all__ = [
    "AccountScimIdentityAdmin",
    "IdentityAdmin",
    "IdentityOperator",
    "ManagedServicePrincipal",
    "WorkspaceIdentityAdmin",
    "identity_admin_from_reference",
]
