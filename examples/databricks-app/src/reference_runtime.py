"""Deployment factory selected by ``TETHER_APP_RUNTIME=reference_runtime:create``."""

from __future__ import annotations

import os

from tether_databricks_example.qualification import qualification_client_settings
from tether_databricks_example.runtime import RuntimeBindings, resource_provider_for_source
from tether_databricks_example.service_caller import DatabricksServiceCallerAuthenticator
from tether_databricks_example.settings import deployment_settings, lakebase_source
from tether_web_api import create_api
from tether_web_ui import create_static_app

from tether.client import ClientSettings, ProvidedStorage

_SETTING_NAMES = (
    "DATABRICKS_APP_PORT",
    "DATABRICKS_HOST",
    "TETHER_PUBLIC_ORIGIN",
    "TETHER_MOUNT_PATH",
    "TETHER_API_PREFIX",
    "TETHER_PG_HOST",
    "TETHER_PG_PORT",
    "TETHER_PG_DATABASE",
    "TETHER_PG_SCHEMA",
    "TETHER_PG_ENDPOINT",
    "TETHER_PG_INFRA_ROLE",
    "TETHER_PG_CAPABILITY_PROFILE",
    "TETHER_PG_SSLROOTCERT",
    "TETHER_SERVICE_CALLER_PRINCIPAL_ID",
    "TETHER_SERVICE_CALLER_DATABASE_ROLE",
    "TETHER_QUALIFICATION_SCENARIO",
    "TETHER_QUALIFICATION_HOLD_SECONDS",
)


def create() -> RuntimeBindings:
    """Map an explicit non-secret allowlist into carrier composition values."""
    values = {name: os.environ[name] for name in _SETTING_NAMES if name in os.environ}  # noqa: TID251
    settings, api_settings, ui_settings = deployment_settings(values)
    infrastructure_actor, source = lakebase_source(values)
    resources = resource_provider_for_source(settings, source)
    binding = settings.resource_bindings[0]

    def client_settings(storage: ProvidedStorage) -> ClientSettings:
        hold_seconds = float(values.get("TETHER_QUALIFICATION_HOLD_SECONDS", "0"))
        scenario = values.get("TETHER_QUALIFICATION_SCENARIO", "basic")
        if scenario == "basic":
            return qualification_client_settings(storage, hold_seconds=hold_seconds)
        if scenario == "shared-session":
            from tether_databricks_example.qualification_shared_session import (
                qualification_shared_session_settings,
            )

            return qualification_shared_session_settings(storage, hold_seconds=hold_seconds)
        raise ValueError("qualification_scenario_unsupported")

    return RuntimeBindings(
        settings=settings,
        client_settings=client_settings,
        infrastructure_actor=infrastructure_actor,
        resource_provider=resources,
        database_binding=binding,
        authenticator=None,
        api_settings=api_settings,
        ui_settings=ui_settings,
        api_factory=create_api,
        ui_factory=create_static_app,
        service_caller_authenticator=_service_caller(values),
    )


def _service_caller(values: dict[str, str]):
    principal_id = values.get("TETHER_SERVICE_CALLER_PRINCIPAL_ID")
    database_role = values.get("TETHER_SERVICE_CALLER_DATABASE_ROLE")
    if (principal_id is None) != (database_role is None):
        raise ValueError("service_caller_settings_must_be_paired")
    if principal_id is None or database_role is None:
        return None
    if database_role == values.get("TETHER_PG_INFRA_ROLE"):
        raise ValueError("service_caller_role_must_be_separate_from_infrastructure")
    return DatabricksServiceCallerAuthenticator(
        workspace_host=values["DATABRICKS_HOST"],
        allowed_principal_id=principal_id,
        actor_id=database_role,
    )


__all__ = ["create"]
