"""Selected live controls receive explicit administrator and runtime inputs."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
import qualify_selected_profiles as qualifier
import selected_profiles_identity as identity
from databricks.sdk import WorkspaceClient


def _set_attribute(module: ModuleType, name: str, value: Any) -> None:
    setattr(module, name, value)


@pytest.mark.parametrize("selected", [False, True], ids=["default-workspace", "explicit-account"])
def test_selected_identity_administrator_is_injected_only_when_requested(
    monkeypatch, tmp_path: Path, selected: bool
) -> None:
    port = object()
    factory = AsyncMock(return_value=port)
    module = ModuleType("selected_profiles_identity_admin")
    _set_attribute(module, "identity_admin_from_reference", factory)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    revocation = AsyncMock(return_value={"revocation_token_unexpired_at_denial": True})
    monkeypatch.setattr(identity, "revocation_and_refusal", revocation)
    primary = cast(WorkspaceClient, object())
    service = cast(
        WorkspaceClient, SimpleNamespace(config=SimpleNamespace(host="https://workspace.test"))
    )
    reference = tmp_path / "administrator.json"
    identity_reference = tmp_path / "identity.json"
    args = argparse.Namespace(
        case=["US-068-AC3"],
        identity_reference=identity_reference,
        service_config_file="fabricated-config",
        service_secret_reference="-".join(("fabricated", "reference")),
        service_profile="fabricated-service",
    )
    fresh_credential = object()
    credential_factory = AsyncMock(return_value=fresh_credential)
    if selected:
        args.identity_admin_reference = reference
    observed, details = asyncio.run(
        qualifier._run_cases(
            args,
            origin="https://example.test",
            prefix="/api",
            settings=SimpleNamespace(),
            values={},
            app_name="fabricated-app",
            app_principal_id="fabricated-infrastructure",
            app=SimpleNamespace(),
            human_workspace=primary,
            service_workspace=service,
            credential_factory=credential_factory,
        )
    )
    assert [case for case, _duration in observed] == ["US-068-AC3"]
    assert details == {
        "US-068-AC3": {
            "administration_mode": "account-scim-azure-cli" if selected else "workspace-scim",
            "authority_scope": "account" if selected else "workspace",
            "target_ownership_verified": True,
            "application_caller_profiles_unchanged": True,
            "revocation_token_unexpired_at_denial": True,
        }
    }
    if selected:
        factory.assert_awaited_once_with(
            reference,
            primary_workspace=primary,
            identity_reference=identity_reference,
            app_principal_id="fabricated-infrastructure",
        )
    else:
        factory.assert_not_called()
    assert revocation.await_args is not None
    refresh = revocation.await_args.kwargs["refresh_credential"]
    assert asyncio.run(refresh()) is fresh_credential
    assert refresh is credential_factory
    credential_factory.assert_awaited_once_with()
    revocation.assert_awaited_once_with(
        "https://example.test",
        "/api",
        admin_workspace=primary,
        service_workspace=service,
        identity_reference=identity_reference,
        app_principal_id="fabricated-infrastructure",
        app_name="fabricated-app",
        identity_admin=port if selected else None,
        refresh_credential=refresh,
    )


def test_identity_administrator_reference_is_an_optional_explicit_cli_path(monkeypatch) -> None:
    arguments = ["qualify_selected_profiles.py"]
    for flag in (
        "profile",
        "service-profile",
        "service-config-file",
        "service-secret-reference",
        "settings-file",
        "app-reference-file",
        "identity-reference",
        "deployment-reference-file",
        "bundle",
        "output",
        "browser-receipt",
    ):
        arguments.extend(["--" + flag, "fabricated"])
    arguments.extend(["--case", "US-068-AC2"])
    monkeypatch.setattr(sys, "argv", arguments)
    assert qualifier._parse_args().identity_admin_reference is None
    arguments.extend(["--identity-admin-reference", "administrator.json"])
    assert qualifier._parse_args().identity_admin_reference == Path("administrator.json")


def test_example_startup_probe_receives_the_selected_bundle(monkeypatch, tmp_path: Path) -> None:
    import selected_profiles_example_runtime as example

    probe = AsyncMock()
    monkeypatch.setattr(example, "selected_example_schema_refusal", probe)
    workspace = cast(WorkspaceClient, object())
    values = {"TETHER_PG_SCHEMA": "fabricated_schema"}
    asyncio.run(
        qualifier._run_cases(
            argparse.Namespace(case=["US-068-AC7"], bundle=tmp_path),
            origin="https://example.test",
            prefix="/api",
            settings=SimpleNamespace(),
            values=values,
            app_name="fabricated-app",
            app_principal_id="fabricated-infrastructure",
            app=SimpleNamespace(),
            human_workspace=cast(WorkspaceClient, object()),
            service_workspace=workspace,
            credential_factory=AsyncMock(),
        )
    )
    probe.assert_awaited_once_with(
        workspace=workspace,
        values=values,
        selected_schema="fabricated_schema",
        bundle=tmp_path,
    )


def test_principal_separation_preserves_actual_database_oracle_observation(monkeypatch) -> None:
    observation = {
        "database_auth_refusal_sqlstate": "28P01",
        "database_oracle_driver_version": "0.32.0",
    }
    probe = AsyncMock(return_value=observation)
    monkeypatch.setattr(identity, "principal_separation", probe)
    _cases, details = asyncio.run(
        qualifier._run_cases(
            argparse.Namespace(case=["US-068-AC4"]),
            origin="https://example.test",
            prefix="/api",
            settings=SimpleNamespace(),
            values={
                "TETHER_PG_INFRA_ROLE": "fabricated-infrastructure",
                "TETHER_SERVICE_CALLER_DATABASE_ROLE": "fabricated-caller",
            },
            app_name="fabricated-app",
            app_principal_id="fabricated-infrastructure",
            app=SimpleNamespace(),
            human_workspace=cast(WorkspaceClient, object()),
            service_workspace=cast(WorkspaceClient, object()),
            credential_factory=AsyncMock(),
        )
    )
    probe.assert_awaited_once()
    assert details == {"US-068-AC4": observation}
