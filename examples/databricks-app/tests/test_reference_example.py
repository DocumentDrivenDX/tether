from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from tether_databricks import DatabricksResources, ResourceRef
from tether_databricks_example.postgres import (
    LakebaseConnectionSource,
    LakebaseSettings,
    WorkspacePostgresClient,
)
from tether_databricks_example.qualification import qualification_client_settings
from tether_databricks_example.service_caller import DatabricksServiceCallerAuthenticator
from tether_databricks_example.settings import deployment_settings
from tether_web_api import ApiFactory
from tether_web_ui import UiFactory

from tether.client import (
    Actor,
    ClientHost,
    ClientSettings,
    Credential,
    LocalJsonlSettings,
    ProvidedStorage,
)


def _bearer(value: str) -> str:
    return " ".join(("Bearer", value))


class _Credential:
    def __init__(self, value: str | None) -> None:
        self.value = value

    async def authorization(self) -> str | None:
        return self.value


def _settings() -> LakebaseSettings:
    return LakebaseSettings(
        workspace_host="https://workspace.example",
        endpoint="resource-reference",
        host="database.example",
        database="tether",
        infra_role="app-principal",
    )


def test_app_configuration_is_explicit_and_nonsecret() -> None:
    app, api, _ui = deployment_settings(
        {
            "DATABRICKS_APP_PORT": "8000",
            "DATABRICKS_HOST": "https://workspace.example",
            "TETHER_PUBLIC_ORIGIN": "https://tether.example",
            "TETHER_PG_HOST": "database.example",
            "TETHER_PG_DATABASE": "tether",
            "TETHER_PG_SCHEMA": "tether_example_schema",
            "TETHER_PG_ENDPOINT": "endpoint-ref",
            "TETHER_PG_INFRA_ROLE": "app-principal",
            "TETHER_PG_CAPABILITY_PROFILE": "qualification",
            "TETHER_API_PREFIX": "/api/v1",
            "TETHER_MOUNT_PATH": "/tether",
        }
    )
    if not (
        app.host == "0.0.0.0"  # noqa: S104
        and app.port == 8000
        and app.path_prefix == "/tether"
        and app.database_profile is not None
        and app.database_profile.schema_name == "tether_example_schema"
        and api.api_prefix == "/api/v1"
        and api.allowed_origins == ("https://tether.example",)
    ):
        raise AssertionError("explicit_settings_not_mapped")


def test_app_origin_is_required_and_mapped_exactly() -> None:
    values = {
        "DATABRICKS_APP_PORT": "8000",
        "DATABRICKS_HOST": "https://workspace.example",
        "TETHER_PG_HOST": "database.example",
        "TETHER_PG_DATABASE": "tether",
        "TETHER_PG_SCHEMA": "tether_example_schema",
        "TETHER_PG_ENDPOINT": "endpoint-ref",
        "TETHER_PG_INFRA_ROLE": "app-principal",
        "TETHER_PG_CAPABILITY_PROFILE": "qualification",
    }
    with pytest.raises(ValueError, match="required_databricks_settings_missing"):
        deployment_settings(values)
    values["TETHER_PUBLIC_ORIGIN"] = "https://ui.example"
    _app, api, _ui = deployment_settings(values)
    if api.allowed_origins != ("https://ui.example",):
        raise AssertionError("public_origin_not_mapped")


def test_infrastructure_and_user_tokens_never_fall_back_to_each_other() -> None:
    infrastructure = cast(WorkspacePostgresClient, SimpleNamespace(postgres=object()))
    captured: list[tuple[str, str]] = []

    def user_client(host: str, token: str, /) -> WorkspacePostgresClient:
        captured.append((host, token))
        return cast(WorkspacePostgresClient, SimpleNamespace(postgres=object()))

    source = LakebaseConnectionSource(
        _settings(),
        database_user=lambda actor: actor.id,
        infrastructure_client=infrastructure,
        user_client_factory=user_client,
    )

    async def scenario() -> None:
        app_client = await source._client_for(Actor(id="app-principal", kind="service"))
        if app_client is not infrastructure:
            raise AssertionError("infrastructure_identity_not_selected")
        user = Actor(
            id="user@example.test",
            credential=cast(Credential, _Credential(_bearer("current-user"))),
        )
        await source._client_for(user)
        if captured != [("https://workspace.example", "current-user")]:
            raise AssertionError("request_user_token_not_selected")
        with pytest.raises(PermissionError, match="user_database_credential_required"):
            await source._client_for(Actor(id="user@example.test"))
        missing = Actor(
            id="user@example.test",
            credential=cast(Credential, _Credential(None)),
        )
        with pytest.raises(PermissionError, match="user_database_credential_required"):
            await source._client_for(missing)

    asyncio.run(scenario())


def test_infrastructure_sdk_selects_only_apps_oauth_m2m(monkeypatch) -> None:
    from databricks import sdk
    from tether_databricks_example.postgres import app_service_workspace_client

    captured: list[dict[str, str]] = []
    sentinel = object()

    def fake_workspace_client(**kwargs: str) -> object:
        captured.append(kwargs)
        return sentinel

    monkeypatch.setattr(sdk, "WorkspaceClient", fake_workspace_client)
    if app_service_workspace_client() is not sentinel:
        raise AssertionError("apps_workspace_client_not_returned")
    if captured != [{"auth_type": "oauth-m2m"}]:
        raise AssertionError("infrastructure_sdk_auth_strategy_not_explicit")


def test_service_caller_requires_workspace_verified_allowlisted_identity(monkeypatch) -> None:
    from tether_databricks_example import service_caller

    calls: list[tuple[str, str, str]] = []

    class FakeWorkspaceClient:
        def __init__(self, *, host: str, token: str, auth_type: str) -> None:
            calls.append((host, token, auth_type))
            self.current_user = SimpleNamespace(
                me=lambda: SimpleNamespace(
                    id="verified-service-id",
                    user_name="service@example.invalid",
                    active=True,
                )
            )

    monkeypatch.setattr(service_caller, "WorkspaceClient", FakeWorkspaceClient)
    authenticator = DatabricksServiceCallerAuthenticator(
        workspace_host="https://workspace.example",
        allowed_principal_id="verified-service-id",
        actor_id="dedicated-database-role",
    )

    async def scenario() -> None:
        identity = await authenticator("fabricated-current-token")
        if identity is None:
            raise AssertionError("verified_service_caller_rejected")
        if identity.actor_id != "dedicated-database-role":
            raise AssertionError("service_caller_role_mapping_missing")
        if identity.principal != "verified-service-id":
            raise AssertionError("verified_principal_not_preserved")
        if calls != [("https://workspace.example", "fabricated-current-token", "pat")]:
            raise AssertionError("token_not_verified_against_explicit_workspace")

    asyncio.run(scenario())


def test_service_caller_rejects_nonmatching_or_inactive_principal(monkeypatch) -> None:
    from tether_databricks_example import service_caller

    class FakeWorkspaceClient:
        def __init__(self, **_kwargs: str) -> None:
            self.current_user = SimpleNamespace(
                me=lambda: SimpleNamespace(id="other-service-id", active=True)
            )

    monkeypatch.setattr(service_caller, "WorkspaceClient", FakeWorkspaceClient)
    authenticator = DatabricksServiceCallerAuthenticator(
        workspace_host="https://workspace.example",
        allowed_principal_id="verified-service-id",
        actor_id="dedicated-database-role",
    )

    async def scenario() -> None:
        if await authenticator("fabricated-token") is not None:
            raise AssertionError("unlisted_service_principal_accepted")

    asyncio.run(scenario())


def test_service_caller_database_role_cannot_be_infrastructure_role() -> None:
    import reference_runtime

    with pytest.raises(
        ValueError, match="service_caller_role_must_be_separate_from_infrastructure"
    ):
        reference_runtime._service_caller(
            {
                "DATABRICKS_HOST": "https://workspace.example",
                "TETHER_PG_INFRA_ROLE": "fabricated-infrastructure-role",
                "TETHER_SERVICE_CALLER_PRINCIPAL_ID": "fabricated-caller-id",
                "TETHER_SERVICE_CALLER_DATABASE_ROLE": "fabricated-infrastructure-role",
            }
        )


def test_qualification_client_sends_through_real_host_and_records_attempt(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = replace(
            qualification_client_settings(cast(ProvidedStorage, object())),
            storage=LocalJsonlSettings(tmp_path),
        )
        async with await ClientHost.open(settings) as host:
            client = host.client(Actor(id="fabricated-user"))
            session = await client.create(spec="qualification")
            handle = await client.send(
                session.id, "fabricated qualification input", spec="qualification"
            )
            result = await handle.result()
            if not any(
                message.role == "assistant"
                and message.content == "Fabricated Tether qualification response."
                for message in result.messages
            ):
                raise AssertionError("qualification_answer_not_recorded")
            call_ids = tuple(
                str(entry.facts["model_call_id"])
                for entry in (await client.entries(session.id)).items
                if entry.kind == "model_call"
            )
            if not call_ids:
                raise AssertionError("qualification_attempt_not_recorded")
            details = await client.call_detail(session.id, call_ids)
            if (
                len(details) != 1
                or details[0].receipt is None
                or details[0].receipt.provider != "fabricated-qualification"
                or details[0].status != "ready"
            ):
                raise AssertionError("qualification_receipt_not_recorded")
            if await client.turn_result(session.id, handle.turn_id) != result:
                raise AssertionError("qualification_turn_result_not_retrievable")

    asyncio.run(scenario())


def test_failed_postgres_open_closes_acquired_provider_after_database_close(monkeypatch) -> None:
    from tether_databricks_example import runtime

    events: list[str] = []

    class Provider:
        async def acquire(self, ref: ResourceRef):
            events.append("acquire")
            return SimpleNamespace(value=lambda *_args, **_kwargs: None, owned=False)

        async def aclose(self) -> tuple[str, ...]:
            events.append("provider-close")
            return ()

    async def failing_open(*_args, **_kwargs):
        events.append("postgres-open")
        raise RuntimeError("fabricated open failure")

    monkeypatch.setattr(runtime.PostgresResources, "open", failing_open)
    app_settings, api_settings, ui_settings = deployment_settings(
        {
            "DATABRICKS_APP_PORT": "8000",
            "DATABRICKS_HOST": "https://workspace.example",
            "TETHER_PUBLIC_ORIGIN": "https://tether.example",
            "TETHER_PG_HOST": "database.example",
            "TETHER_PG_DATABASE": "tether",
            "TETHER_PG_SCHEMA": "tether_example_schema",
            "TETHER_PG_ENDPOINT": "endpoint-ref",
            "TETHER_PG_INFRA_ROLE": "app-principal",
            "TETHER_PG_CAPABILITY_PROFILE": "qualification",
        }
    )
    bindings = runtime.RuntimeBindings(
        settings=app_settings,
        client_settings=lambda _storage: cast(ClientSettings, None),
        infrastructure_actor=Actor(id="app-principal", kind="service"),
        resource_provider=cast(DatabricksResources, Provider()),
        database_binding=app_settings.resource_bindings[0],
        authenticator=None,
        api_settings=api_settings,
        ui_settings=ui_settings,
        api_factory=cast(ApiFactory, object()),
        ui_factory=cast(UiFactory, object()),
    )

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="fabricated open failure"):
            await runtime.build_reference_app(
                bindings,
                api_factory=cast(ApiFactory, object()),
                ui_factory=cast(UiFactory, object()),
            )
        if events != ["acquire", "postgres-open", "provider-close"]:
            raise AssertionError("failed_construction_cleanup_order_invalid")

    asyncio.run(scenario())
