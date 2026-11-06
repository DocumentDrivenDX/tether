"""Account-SCIM identity-admin adapter contract controls."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import selected_profiles_identity_admin as admin_module
from databricks.sdk import WorkspaceClient


def _reference(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mode": "account-scim-azure-cli",
                "account_host": "https://accounts.azuredatabricks.net",
                "account_id": "fabricated-account",
                "operator_account_user_id": "101",
                "workspace_id": 202,
            }
        ),
        encoding="utf-8",
    )


def _workspace(user_id: str, username: str) -> WorkspaceClient:
    user = SimpleNamespace(id=user_id, user_name=username)
    return cast(
        WorkspaceClient,
        SimpleNamespace(
            current_user=SimpleNamespace(me=lambda: user),
            get_workspace_id=lambda: 202,
        ),
    )


def _fake_account() -> Any:
    return SimpleNamespace(
        config=SimpleNamespace(
            account_id="fabricated-account",
            host="https://accounts.azuredatabricks.net",
        )
    )


def _wire_factory(
    monkeypatch: pytest.MonkeyPatch, *, valid: bool = True, active: bool = True
) -> tuple[Any, list[Any]]:
    account = _fake_account()
    calls: list[Any] = []
    monkeypatch.setattr(
        admin_module,
        "_read_reference",
        lambda _path: (
            "https://accounts.azuredatabricks.net",
            "fabricated-account",
            "101",
            202,
        ),
    )
    monkeypatch.setattr(admin_module, "_read_target", lambda _path: ("303", "app-303"))
    monkeypatch.setattr(admin_module, "_azure_cli_username", lambda: "operator@example.test")
    monkeypatch.setattr(
        admin_module,
        "Config",
        lambda **kwargs: calls.append(("config", kwargs)) or SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        admin_module,
        "AccountClient",
        lambda **kwargs: calls.append(("account", kwargs)) or account,
    )
    monkeypatch.setattr(
        admin_module,
        "_account_user",
        lambda _account, _user: (
            "101",
            "operator@example.test",
            valid,
            active,
        ),
    )
    monkeypatch.setattr(
        admin_module,
        "_account_service_principal",
        lambda _account, principal: admin_module.ManagedServicePrincipal(
            principal, "app-303", True
        ),
    )
    account.workspaces = SimpleNamespace(
        get=lambda workspace_id: SimpleNamespace(
            workspace_id=workspace_id,
            account_id="fabricated-account",
        ),
        list=lambda: iter([SimpleNamespace(workspace_id=202)]),
    )
    account.workspace_assignment = SimpleNamespace(
        list=lambda workspace_id: iter(
            [
                SimpleNamespace(
                    principal=SimpleNamespace(principal_id=303),
                    permissions=["USER"],
                )
            ]
            if workspace_id == 202
            else []
        )
    )
    return account, calls


def test_factory_binds_existing_account_operator_and_owned_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    account, calls = _wire_factory(monkeypatch)
    # Account-admin authority may be group-mediated; no direct workspace
    # assignment lookup for the operator is required by this adapter.
    assert not hasattr(account, "iam_v2")

    class Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"id": "303", "applicationId": "app-303", "active": True}

    monkeypatch.setattr(admin_module, "_scim_request", lambda *_args, **_kwargs: Response())
    reference = tmp_path / "admin.json"
    identity_reference = tmp_path / "identity.json"
    _reference(reference)
    identity_reference.write_text("{}", encoding="utf-8")

    port = asyncio.run(
        admin_module.identity_admin_from_reference(
            reference,
            primary_workspace=_workspace("operator-id", "operator@example.test"),
            identity_reference=identity_reference,
            app_principal_id="app-infrastructure",
        )
    )

    assert port.operator().account_admin is True
    assert port.operator().workspace_id == 202
    assert port.get_service_principal("303") == admin_module.ManagedServicePrincipal(
        "303", "app-303", True
    )
    assert calls[0] == (
        "config",
        {
            "host": "https://accounts.azuredatabricks.net",
            "account_id": "fabricated-account",
            "auth_type": "azure-cli",
            "http_timeout_seconds": 15,
            "retry_timeout_seconds": 10,
        },
    )
    assert calls[1][0] == "account"
    assert calls[1][1]["config"].auth_type == "azure-cli"


@pytest.mark.parametrize(
    "record",
    [
        {"id": "fabricated-other", "applicationId": "app-303", "active": False},
        {"id": "303", "applicationId": "app-303", "active": 0},
        {"id": "303", "applicationId": "app-303", "active": "false"},
        {"id": "303", "applicationId": "app-303"},
    ],
)
def test_account_service_principal_state_is_exact_and_boolean(
    monkeypatch: pytest.MonkeyPatch, record: dict[str, Any]
) -> None:
    port = admin_module.AccountScimIdentityAdmin(
        _fake_account(),
        account_id="fabricated-account",
        account_host="https://accounts.azuredatabricks.net",
        operator=admin_module.IdentityOperator("101", None, True, 202),
    )

    class Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return record

    monkeypatch.setattr(port, "_request", lambda *_args, **_kwargs: Response())
    with pytest.raises(RuntimeError, match="identity_admin_service_principal_invalid"):
        port.get_service_principal("303")


@pytest.mark.parametrize(
    (
        "primary_name",
        "operator_admin",
        "operator_active",
        "app_id",
        "expected",
    ),
    [
        (
            "other@example.test",
            True,
            True,
            "app-infrastructure",
            "identity_admin_caller_mismatch",
        ),
        (
            "operator@example.test",
            False,
            True,
            "app-infrastructure",
            "identity_admin_scope_not_verified",
        ),
        (
            "operator@example.test",
            True,
            False,
            "app-infrastructure",
            "identity_admin_scope_not_verified",
        ),
        (
            "operator@example.test",
            True,
            True,
            "app-303",
            "identity_admin_target_not_isolated",
        ),
    ],
)
def test_factory_refuses_caller_role_or_target_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    primary_name: str,
    operator_admin: bool,
    operator_active: bool,
    app_id: str,
    expected: str,
) -> None:
    _account, _calls = _wire_factory(monkeypatch, valid=operator_admin, active=operator_active)
    reference = tmp_path / "admin.json"
    identity_reference = tmp_path / "identity.json"
    _reference(reference)
    identity_reference.write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match=expected):
        asyncio.run(
            admin_module.identity_admin_from_reference(
                reference,
                primary_workspace=_workspace("operator-id", primary_name),
                identity_reference=identity_reference,
                app_principal_id=app_id,
            )
        )


def test_reference_requires_exact_nonsecret_schema(tmp_path: Path) -> None:
    reference = tmp_path / "admin.json"
    _reference(reference)
    assert admin_module._read_reference(reference)[-1] == 202
    value = json.loads(reference.read_text(encoding="utf-8"))
    value["unexpected"] = "fabricated"
    reference.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(RuntimeError, match="identity_admin_reference_invalid"):
        admin_module._read_reference(reference)

    value.pop("unexpected")
    value["schema_version"] = 2
    reference.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(RuntimeError, match="identity_admin_reference_invalid"):
        admin_module._read_reference(reference)


def test_factory_requires_primary_host_and_exact_target_workspace_assignment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    account, _calls = _wire_factory(monkeypatch)
    account.workspaces.list = lambda: iter(
        [SimpleNamespace(workspace_id=202), SimpleNamespace(workspace_id=404)]
    )
    account.workspace_assignment.list = lambda workspace_id: iter(
        [SimpleNamespace(principal=SimpleNamespace(principal_id=303), permissions=["USER"])]
    )
    reference = tmp_path / "admin.json"
    identity_reference = tmp_path / "identity.json"
    _reference(reference)
    identity_reference.write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="identity_admin_target_workspace_assignment_mismatch"):
        asyncio.run(
            admin_module.identity_admin_from_reference(
                reference,
                primary_workspace=_workspace("operator-id", "operator@example.test"),
                identity_reference=identity_reference,
                app_principal_id="app-infrastructure",
            )
        )


def test_factory_rejects_target_workspace_admin_permission(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    account, _calls = _wire_factory(monkeypatch)
    account.workspace_assignment.list = lambda workspace_id: iter(
        [SimpleNamespace(principal=SimpleNamespace(principal_id=303), permissions=["ADMIN"])]
        if workspace_id == 202
        else []
    )
    reference = tmp_path / "admin.json"
    identity_reference = tmp_path / "identity.json"
    _reference(reference)
    identity_reference.write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="identity_admin_target_workspace_permission_invalid"):
        asyncio.run(
            admin_module.identity_admin_from_reference(
                reference,
                primary_workspace=_workspace("operator-id", "operator@example.test"),
                identity_reference=identity_reference,
                app_principal_id="app-infrastructure",
            )
        )


def test_factory_rejects_host_resolving_to_another_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _wire_factory(monkeypatch)
    reference = tmp_path / "admin.json"
    identity_reference = tmp_path / "identity.json"
    _reference(reference)
    identity_reference.write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="identity_admin_workspace_host_mismatch"):
        asyncio.run(
            admin_module.identity_admin_from_reference(
                reference,
                primary_workspace=cast(
                    WorkspaceClient,
                    SimpleNamespace(
                        current_user=SimpleNamespace(
                            me=lambda: SimpleNamespace(
                                id="operator-id", user_name="operator@example.test"
                            )
                        ),
                        get_workspace_id=lambda: 999,
                    ),
                ),
                identity_reference=identity_reference,
                app_principal_id="app-infrastructure",
            )
        )


def test_account_scim_adapter_uses_explicit_scim_21_target_and_patch(monkeypatch) -> None:
    account = _fake_account()
    admin = admin_module.AccountScimIdentityAdmin(
        cast(Any, account),
        account_id="fabricated-account",
        account_host="https://accounts.azuredatabricks.net",
        operator=admin_module.IdentityOperator("101", "op@example.test", True, 202),
    )
    requests: list[tuple[str, str, Any]] = []

    class Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"id": "303", "applicationId": "app-303", "active": True}

    def request(_account, _host: str, method: str, path: str, *, json=None):
        requests.append((method, path, json))
        return Response()

    monkeypatch.setattr(admin_module, "_scim_request", request)
    assert admin.get_service_principal("303").active is True
    admin.set_service_principal_active("303", False)
    admin.set_service_principal_active("303", True)
    assert requests[0][1] == "/api/2.1/accounts/fabricated-account/scim/v2/ServicePrincipals/303"
    assert requests[1][0] == "PATCH"
    assert requests[1][2]["Operations"] == [
        {"op": "replace", "path": "active", "value": [{"value": "false"}]}
    ]
    assert requests[2][2]["Operations"] == [
        {"op": "replace", "path": "active", "value": [{"value": "true"}]}
    ]


def test_account_scim_patch_refusal_is_fixed_and_fail_closed(monkeypatch) -> None:
    account = _fake_account()
    admin = admin_module.AccountScimIdentityAdmin(
        cast(Any, account),
        account_id="fabricated-account",
        account_host="https://accounts.azuredatabricks.net",
        operator=admin_module.IdentityOperator("101", "op@example.test", True, 202),
    )

    class Response:
        status_code = 403

    monkeypatch.setattr(admin_module, "_scim_request", lambda *_args, **_kwargs: Response())
    with pytest.raises(RuntimeError, match="identity_admin_patch_refused"):
        admin.set_service_principal_active("303", False)


def test_account_scim_request_uses_documented_content_type(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs == {"timeout": 20, "trust_env": False, "verify": True}

        def __enter__(self):
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def request(self, method: str, url: str, **kwargs: Any):
            captured.update(method=method, url=url, **kwargs)
            return SimpleNamespace(status_code=204)

    monkeypatch.setattr(admin_module.httpx, "Client", Client)
    account = SimpleNamespace(
        config=SimpleNamespace(
            authenticate=lambda: {"Authorization": " ".join(("Bearer", "fabricated"))}
        )
    )
    result = admin_module._scim_request(
        cast(Any, account),
        "https://accounts.azuredatabricks.net",
        "PATCH",
        "/api/2.1/accounts/fabricated/scim/v2/ServicePrincipals/303",
        json={"active": True},
    )
    assert result.status_code == 204
    assert captured["headers"]["Content-Type"] == "application/scim+json"


@pytest.mark.parametrize(
    ("roles", "active", "expected_admin"),
    [
        ([{"value": "account_admin"}], True, True),
        ([{"value": "workspace_admin"}], True, False),
        ([{"value": "account_admin"}], False, True),
    ],
)
def test_account_user_requires_exact_account_admin_role_and_reports_active(
    monkeypatch: pytest.MonkeyPatch,
    roles: list[dict[str, str]],
    active: bool,
    expected_admin: bool,
) -> None:
    account = _fake_account()

    class Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "id": "101",
                "userName": "operator@example.test",
                "roles": roles,
                "active": active,
            }

    monkeypatch.setattr(admin_module, "_scim_request", lambda *_args, **_kwargs: Response())
    observed = admin_module._account_user(cast(Any, account), "101")
    assert observed == ("101", "operator@example.test", expected_admin, active)
