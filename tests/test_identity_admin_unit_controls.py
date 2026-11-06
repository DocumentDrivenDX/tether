"""Unit controls for the identity-admin adapters, readers and scope checks (fabricated)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import selected_profiles_identity_admin as admin_module
from databricks.sdk import AccountClient, WorkspaceClient

_ACCOUNT_HOST = "https://accounts.azuredatabricks.net"


def _spec(**override: object) -> dict[str, object]:
    return {
        "schema_version": 1,
        "mode": "account-scim-azure-cli",
        "account_host": _ACCOUNT_HOST,
        "account_id": "fabricated-account",
        "operator_account_user_id": "101",
        "workspace_id": 202,
        **override,
    }


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_read_reference_returns_pinned_account_scope(tmp_path: Path) -> None:
    assert admin_module._read_reference(_write(tmp_path / "r.json", _spec())) == (
        _ACCOUNT_HOST,
        "fabricated-account",
        "101",
        202,
    )
    host_with_slash = _spec(account_host=_ACCOUNT_HOST.upper().replace("HTTPS", "https") + "/")
    assert admin_module._read_reference(_write(tmp_path / "s.json", host_with_slash))[0] == (
        _ACCOUNT_HOST
    )


@pytest.mark.parametrize(
    "mutation",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"mode": "other"},
        {"account_host": "http://accounts.azuredatabricks.net"},
        {"account_host": "https://evil.example"},
        {"account_host": "https://accounts.azuredatabricks.net/x"},
        {"account_host": "https://accounts.azuredatabricks.net?q=1"},
        {"account_host": "https://accounts.azuredatabricks.net#f"},
        {"account_host": 5},
        {"account_id": ""},
        {"account_id": "a/b"},
        {"account_id": 5},
        {"operator_account_user_id": "0"},
        {"operator_account_user_id": "12a"},
        {"operator_account_user_id": 101},
        {"operator_account_user_id": chr(0x661) + chr(0x660)},
        {"workspace_id": 0},
        {"workspace_id": True},
        {"workspace_id": "202"},
    ],
)
def test_read_reference_refuses_invalid_scope(tmp_path: Path, mutation: dict[str, object]) -> None:
    with pytest.raises(RuntimeError, match="identity_admin_reference_invalid"):
        admin_module._read_reference(_write(tmp_path / "r.json", _spec(**mutation)))


def test_read_reference_refuses_unreadable_malformed_or_extra_keys(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    for path in (tmp_path / "missing.json", bad, _write(tmp_path / "list.json", [])):
        with pytest.raises(RuntimeError, match="identity_admin_reference_invalid"):
            admin_module._read_reference(path)
    with pytest.raises(RuntimeError, match="identity_admin_reference_invalid"):
        admin_module._read_reference(_write(tmp_path / "extra.json", {**_spec(), "extra": 1}))


def test_read_target_returns_ids_and_refuses_invalid(tmp_path: Path) -> None:
    ok = _write(tmp_path / "t.json", {"id": "303", "application_id": "app-1", "x": 1})
    assert admin_module._read_target(ok) == ("303", "app-1")
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    for path in (
        tmp_path / "missing.json",
        bad,
        _write(tmp_path / "list.json", []),
        _write(tmp_path / "noid.json", {"application_id": "a"}),
        _write(tmp_path / "emptyid.json", {"id": "", "application_id": "a"}),
        _write(tmp_path / "noapp.json", {"id": "1"}),
        _write(tmp_path / "intapp.json", {"id": "1", "application_id": 3}),
    ):
        with pytest.raises(RuntimeError, match="identity_admin_target_invalid"):
            admin_module._read_target(path)


def test_account_client_is_pinned_to_azure_cli_with_bounded_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def config(**kwargs: Any) -> str:
        seen.update(kwargs)
        return "config"

    monkeypatch.setattr(admin_module, "Config", config)
    monkeypatch.setattr(admin_module, "AccountClient", lambda config: ("account", config))
    assert admin_module._account_client(_ACCOUNT_HOST, "acct") == ("account", "config")
    assert seen == {
        "host": _ACCOUNT_HOST,
        "account_id": "acct",
        "auth_type": "azure-cli",
        "http_timeout_seconds": 15,
        "retry_timeout_seconds": 10,
    }


def _az(monkeypatch: pytest.MonkeyPatch, *, which: str | None, run: Any) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> Any:
        calls.append(command)
        assert kwargs["timeout"] == 15
        assert kwargs["check"] is True
        return run()

    monkeypatch.setattr(admin_module.shutil, "which", lambda name: which)
    monkeypatch.setattr(admin_module.subprocess, "run", fake_run)
    return calls


def test_azure_cli_username_uses_clean_environment_and_fixed_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _az(
        monkeypatch,
        which="/fabricated/az",
        run=lambda: SimpleNamespace(stdout="  Operator@Example.Test \n"),
    )
    assert admin_module._azure_cli_username() == "Operator@Example.Test"
    command = calls[0]
    assert command[:2] == ["/usr/bin/env", "-i"]
    assert command[-6:] == ["account", "show", "--query", "user.name", "--output", "tsv"]
    assert "/fabricated/az" in command


@pytest.mark.parametrize("stdout", ["", "  \n", "a\nb"])
def test_azure_cli_username_refuses_empty_or_multiline_identity(
    monkeypatch: pytest.MonkeyPatch, stdout: str
) -> None:
    _az(monkeypatch, which="/fabricated/az", run=lambda: SimpleNamespace(stdout=stdout))
    with pytest.raises(RuntimeError, match="identity_admin_azure_cli_identity_unavailable"):
        admin_module._azure_cli_username()


def test_azure_cli_username_refuses_missing_or_failing_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    _az(monkeypatch, which=None, run=lambda: None)
    with pytest.raises(RuntimeError, match="identity_admin_azure_cli_unavailable"):
        admin_module._azure_cli_username()

    def fail() -> None:
        raise subprocess.CalledProcessError(1, "az")

    _az(monkeypatch, which="/fabricated/az", run=fail)
    with pytest.raises(RuntimeError, match="identity_admin_azure_cli_unavailable"):
        admin_module._azure_cli_username()

    def missing() -> None:
        raise FileNotFoundError("az")

    _az(monkeypatch, which="/fabricated/az", run=missing)
    with pytest.raises(RuntimeError, match="identity_admin_azure_cli_unavailable"):
        admin_module._azure_cli_username()


class _Principals:
    def __init__(self, value: object) -> None:
        self.value = value
        self.patches: list[dict[str, Any]] = []

    def get(self, principal_id: str) -> object:
        return self.value

    def patch(self, principal_id: str, **kwargs: Any) -> None:
        self.patches.append({"id": principal_id, **kwargs})


def _workspace_admin(
    value: object = None,
) -> tuple[admin_module.WorkspaceIdentityAdmin, _Principals]:
    principals = _Principals(value)
    user = SimpleNamespace(id=7, user_name="Operator@Example.Test")
    workspace = SimpleNamespace(
        current_user=SimpleNamespace(me=lambda: user), service_principals=principals
    )
    return admin_module.WorkspaceIdentityAdmin(cast(WorkspaceClient, workspace)), principals


def test_workspace_admin_operator_is_never_account_admin() -> None:
    operator = _workspace_admin()[0].operator()
    assert operator == admin_module.IdentityOperator("7", "Operator@Example.Test", False, None)


def test_workspace_admin_operator_tolerates_missing_id() -> None:
    workspace = SimpleNamespace(current_user=SimpleNamespace(me=lambda: SimpleNamespace(id=None)))
    admin = admin_module.WorkspaceIdentityAdmin(cast(WorkspaceClient, workspace))
    assert admin.operator() == admin_module.IdentityOperator("", None, False, None)


def test_workspace_admin_reads_exact_principal() -> None:
    value = SimpleNamespace(id="303", application_id="app-1", active=True)
    admin, _ = _workspace_admin(value)
    assert admin.get_service_principal("303") == admin_module.ManagedServicePrincipal(
        "303", "app-1", True
    )


@pytest.mark.parametrize(
    "value",
    [
        SimpleNamespace(id="404", application_id="app-1", active=True),
        SimpleNamespace(id="303", application_id="", active=True),
        SimpleNamespace(id="303", application_id="app-1", active="true"),
        SimpleNamespace(id="303", application_id="app-1"),
    ],
)
def test_workspace_admin_refuses_mismatched_or_malformed_principal(value: object) -> None:
    admin, _ = _workspace_admin(value)
    with pytest.raises(RuntimeError, match="identity_admin_service_principal_invalid"):
        admin.get_service_principal("303")


@pytest.mark.parametrize("active", [True, False])
def test_workspace_admin_patches_active_with_replace_operation(active: bool) -> None:
    admin, principals = _workspace_admin()
    admin.set_service_principal_active("303", active)
    (patch,) = principals.patches
    assert patch["id"] == "303"
    (operation,) = patch["operations"]
    assert operation.value == {"active": active}
    assert operation.op.value == "replace"
    assert len(patch["schemas"]) == 1


class _Response:
    def __init__(self, status_code: int, payload: object = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload


def _scim_admin(
    monkeypatch: pytest.MonkeyPatch, response: _Response
) -> tuple[admin_module.AccountScimIdentityAdmin, list[tuple[str, str, Any]]]:
    calls: list[tuple[str, str, Any]] = []

    def request(account: Any, host: str, method: str, path: str, *, json: Any = None) -> _Response:
        calls.append((method, path, json))
        return response

    monkeypatch.setattr(admin_module, "_scim_request", request)
    operator = admin_module.IdentityOperator("101", "op@example.test", True, 202)
    return (
        admin_module.AccountScimIdentityAdmin(
            cast(AccountClient, object()),
            account_id="acct/1",
            account_host=_ACCOUNT_HOST,
            operator=operator,
        ),
        calls,
    )


def test_account_scim_admin_reports_operator_and_reads_encoded_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = {"id": "303", "applicationId": "app-1", "active": False}
    scim, calls = _scim_admin(monkeypatch, _Response(200, value))
    assert scim.operator().account_admin is True
    assert scim.get_service_principal("303") == admin_module.ManagedServicePrincipal(
        "303", "app-1", False
    )
    assert calls == [("GET", "/api/2.1/accounts/acct%2F1/scim/v2/ServicePrincipals/303", None)]


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (_Response(404), "identity_admin_lookup_refused"),
        (_Response(200, ["not", "a", "mapping"]), "identity_admin_response_invalid"),
        (
            _Response(200, {"id": "other", "applicationId": "a", "active": True}),
            "identity_admin_service_principal_invalid",
        ),
        (
            _Response(200, {"id": "303", "applicationId": "a", "active": "yes"}),
            "identity_admin_service_principal_invalid",
        ),
    ],
)
def test_account_scim_admin_refuses_bad_lookup(
    monkeypatch: pytest.MonkeyPatch, response: _Response, code: str
) -> None:
    scim, _ = _scim_admin(monkeypatch, response)
    with pytest.raises(RuntimeError, match=code):
        scim.get_service_principal("303")


def test_account_scim_admin_refuses_empty_or_foreign_resource_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scim, calls = _scim_admin(monkeypatch, _Response(200, {}))
    with pytest.raises(RuntimeError, match="identity_admin_target_invalid"):
        scim.get_service_principal("")
    with pytest.raises(RuntimeError, match="identity_admin_target_invalid"):
        scim._scim_path("Groups", "1")
    with pytest.raises(RuntimeError, match="identity_admin_target_invalid"):
        scim.set_service_principal_active("", True)
    assert calls == []


@pytest.mark.parametrize(("active", "text"), [(True, "true"), (False, "false")])
def test_account_scim_admin_patch_body_and_accepted_statuses(
    monkeypatch: pytest.MonkeyPatch, active: bool, text: str
) -> None:
    scim, calls = _scim_admin(monkeypatch, _Response(204))
    scim.set_service_principal_active("303", active)
    method, path, body = calls[0]
    assert (method, path.endswith("/ServicePrincipals/303")) == ("PATCH", True)
    assert body["Operations"] == [{"op": "replace", "path": "active", "value": [{"value": text}]}]


def _account_with(
    workspaces: list[Any], assignments: dict[int, list[Any]], record: Any = None
) -> Any:
    return SimpleNamespace(
        workspaces=SimpleNamespace(
            list=lambda: iter(workspaces),
            get=lambda workspace_id: record,
        ),
        workspace_assignment=SimpleNamespace(
            list=lambda workspace_id: iter(assignments[workspace_id])
        ),
    )


def _assignment(principal_id: int, permissions: Any) -> Any:
    return SimpleNamespace(
        principal=SimpleNamespace(principal_id=principal_id), permissions=permissions
    )


def test_listed_workspace_ids_are_unique_bounded_integers() -> None:
    records = [SimpleNamespace(workspace_id=1), SimpleNamespace(workspace_id=2)]
    assert admin_module._listed_workspace_ids(_account_with(records, {})) == (1, 2)


@pytest.mark.parametrize(
    ("records", "code"),
    [
        (
            [SimpleNamespace(workspace_id=1), SimpleNamespace(workspace_id=1)],
            "identity_admin_workspace_list_invalid",
        ),
        ([SimpleNamespace(workspace_id="1")], "identity_admin_workspace_list_invalid"),
        ([SimpleNamespace(workspace_id=True)], "identity_admin_workspace_list_invalid"),
        ([SimpleNamespace()], "identity_admin_workspace_list_invalid"),
        (
            [SimpleNamespace(workspace_id=i) for i in range(1, 1003)],
            "identity_admin_workspace_list_exceeds_bound",
        ),
    ],
)
def test_listed_workspace_ids_refuse_invalid_lists(records: list[Any], code: str) -> None:
    with pytest.raises(RuntimeError, match=code):
        admin_module._listed_workspace_ids(_account_with(records, {}))


def test_target_assignment_ids_find_only_the_target_with_user_permission() -> None:
    account = _account_with(
        [],
        {
            1: [_assignment(9, ["USER"]), _assignment(5, ["USER"])],
            2: [_assignment(9, [SimpleNamespace(value="user")])],
            3: [_assignment(5, ["USER"])],
        },
    )
    assert admin_module._target_assignment_ids(account, (1, 2, 3), 9) == [1, 2]
    assert admin_module._target_assignment_ids(account, (1, 2, 3), 77) == []


def test_target_assignment_ids_refuse_unbounded_assignment_pages() -> None:
    account = _account_with([], {1: [_assignment(i, ["USER"]) for i in range(1001)]})
    with pytest.raises(RuntimeError, match="identity_admin_assignment_list_exceeds_bound"):
        admin_module._target_assignment_ids(account, (1,), 5)


@pytest.mark.parametrize("permissions", [None, [], ["ADMIN"], ["USER", "ADMIN"]])
def test_workspace_permission_must_be_exactly_user(permissions: Any) -> None:
    with pytest.raises(RuntimeError, match="identity_admin_target_workspace_permission_invalid"):
        admin_module._require_user_workspace_permission(SimpleNamespace(permissions=permissions))
    assert admin_module._require_user_workspace_permission(_assignment(1, ["USER"])) is True


def _bind(account: Any, target: str = "9", workspace_id: int = 202, primary: int = 202) -> None:
    admin_module._verify_target_workspace_binding(
        account, "acct", workspace_id, target, lambda: primary
    )


def _bound_account(**record: Any) -> Any:
    fields = {"workspace_id": 202, "account_id": "acct"} | record
    return _account_with(
        [SimpleNamespace(workspace_id=202)],
        {202: [_assignment(9, ["USER"])]},
        SimpleNamespace(**fields),
    )


def test_target_workspace_binding_accepts_exact_single_assignment() -> None:
    _bind(_bound_account())


@pytest.mark.parametrize(
    ("account", "target", "primary", "code"),
    [
        (_bound_account(), "9", 999, "identity_admin_workspace_host_mismatch"),
        (_bound_account(workspace_id=1), "9", 202, "identity_admin_workspace_record_mismatch"),
        (_bound_account(account_id="other"), "9", 202, "identity_admin_workspace_record_mismatch"),
        (_bound_account(), "abc", 202, "identity_admin_target_invalid"),
        (_bound_account(), "0", 202, "identity_admin_target_invalid"),
        (
            _account_with(
                [SimpleNamespace(workspace_id=1)],
                {},
                SimpleNamespace(workspace_id=202, account_id="acct"),
            ),
            "9",
            202,
            "identity_admin_workspace_list_invalid",
        ),
        (
            _account_with(
                [SimpleNamespace(workspace_id=202)],
                {202: []},
                SimpleNamespace(workspace_id=202, account_id="acct"),
            ),
            "9",
            202,
            "identity_admin_target_workspace_assignment_mismatch",
        ),
    ],
)
def test_target_workspace_binding_refuses_scope_drift(
    account: Any, target: str, primary: int, code: str
) -> None:
    with pytest.raises(RuntimeError, match=code):
        _bind(account, target=target, primary=primary)


def _scim_account(authorization: object = "Bearer fabricated", **config: Any) -> Any:
    values = {"account_id": "acct", "host": _ACCOUNT_HOST} | config
    return SimpleNamespace(
        config=SimpleNamespace(authenticate=lambda: {"Authorization": authorization}, **values)
    )


def test_scim_get_builds_account_scoped_request(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[Any, ...]] = []

    def request(account: Any, host: str, method: str, path: str, **kwargs: Any) -> _Response:
        seen.append((host, method, path, kwargs))
        return _Response(200, {"id": "1"})

    monkeypatch.setattr(admin_module, "_scim_request", request)
    assert admin_module._scim_get(_scim_account(), "Users", "a b") == {"id": "1"}
    assert seen == [(_ACCOUNT_HOST, "GET", "/api/2.1/accounts/acct/scim/v2/Users/a%20b", {})]


@pytest.mark.parametrize(
    ("resource", "resource_id", "config", "response", "code"),
    [
        ("Groups", "1", {}, _Response(200, {}), "identity_admin_target_invalid"),
        ("Users", "", {}, _Response(200, {}), "identity_admin_target_invalid"),
        (
            "Users",
            "1",
            {"account_id": None},
            _Response(200, {}),
            "identity_admin_reference_invalid",
        ),
        ("Users", "1", {"host": None}, _Response(200, {}), "identity_admin_reference_invalid"),
        ("Users", "1", {}, _Response(403), "identity_admin_lookup_refused"),
        ("Users", "1", {}, _Response(200, "text"), "identity_admin_response_invalid"),
    ],
)
def test_scim_get_refuses_invalid_scope_or_response(
    monkeypatch: pytest.MonkeyPatch,
    resource: str,
    resource_id: str,
    config: dict[str, Any],
    response: _Response,
    code: str,
) -> None:
    monkeypatch.setattr(admin_module, "_scim_request", lambda *_a, **_k: response)
    with pytest.raises(RuntimeError, match=code):
        admin_module._scim_get(_scim_account(**config), resource, resource_id)


@pytest.mark.parametrize("authorization", [None, "", "Basic abc", 5])
def test_scim_request_refuses_non_bearer_credentials(authorization: object) -> None:
    with pytest.raises(RuntimeError, match="identity_admin_credential_unavailable"):
        admin_module._scim_request(_scim_account(authorization), _ACCOUNT_HOST, "GET", "/x")


def test_scim_request_accepts_lowercase_authorization_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Client:
        def __init__(self, **_kwargs: Any) -> None:
            return None

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def request(self, method: str, url: str, **kwargs: Any) -> str:
            captured.update(method=method, url=url, **kwargs)
            return "response"

    monkeypatch.setattr(admin_module.httpx, "Client", Client)
    account = SimpleNamespace(
        config=SimpleNamespace(authenticate=lambda: {"authorization": "Bearer fabricated"})
    )
    assert admin_module._scim_request(cast(Any, account), _ACCOUNT_HOST + "/", "GET", "/p") == (
        "response"
    )
    assert captured["url"] == _ACCOUNT_HOST + "/p"
    assert captured["headers"]["Authorization"] == "Bearer fabricated"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(" Op@Example.TEST ", "op@example.test"), ("", None), ("   ", None), (None, None), (5, None)],
)
def test_normalize_username(value: object, expected: str | None) -> None:
    assert admin_module._normalize_username(value) == expected


def test_required_field_helpers_accept_mappings_and_objects() -> None:
    assert admin_module._required_string({"k": "v"}, "k", "err") == "v"
    assert admin_module._required_string(SimpleNamespace(k="v"), "k", "err") == "v"
    assert admin_module._required_boolean({"k": False}, "k", "err") is False
    assert admin_module._required_boolean(SimpleNamespace(k=True), "k", "err") is True
    for bad in ({}, {"k": ""}, {"k": 1}):
        with pytest.raises(RuntimeError, match="err"):
            admin_module._required_string(bad, "k", "err")
    for bad in ({}, {"k": 1}, {"k": "true"}):
        with pytest.raises(RuntimeError, match="err"):
            admin_module._required_boolean(bad, "k", "err")
