"""Allowlisted non-secret deployment settings for the reference app."""

from __future__ import annotations

from collections.abc import Mapping

from tether_databricks import (
    DatabaseProfileRef,
    DatabricksAppSettings,
    ResourceRef,
)
from tether_web_api import ApiSettings
from tether_web_ui import UiSettings

from tether.client import Actor

from .postgres import LakebaseConnectionSource, LakebaseSettings, app_service_workspace_client


def deployment_settings(
    values: Mapping[str, str],
) -> tuple[DatabricksAppSettings, ApiSettings, UiSettings]:
    """Parse only public settings; secrets remain in explicit credential providers."""
    required = (
        "DATABRICKS_APP_PORT",
        "DATABRICKS_HOST",
        "TETHER_PUBLIC_ORIGIN",
        "TETHER_PG_HOST",
        "TETHER_PG_DATABASE",
        "TETHER_PG_SCHEMA",
        "TETHER_PG_ENDPOINT",
        "TETHER_PG_INFRA_ROLE",
        "TETHER_PG_CAPABILITY_PROFILE",
    )
    if any(not values.get(name) for name in required):
        raise ValueError("required_databricks_settings_missing")
    endpoint = values["TETHER_PG_ENDPOINT"]
    schema = values["TETHER_PG_SCHEMA"]
    profile = DatabaseProfileRef(
        source_id=endpoint,
        schema=schema,
        role_mode="caller",
        tls_mode="required",
        durability_profile="databricks-lakebase-pg18",
        migration_owner=values["TETHER_PG_INFRA_ROLE"],
        capability_profile=values["TETHER_PG_CAPABILITY_PROFILE"],
    )
    binding = ResourceRef(
        name=endpoint,
        kind="lakebase-postgres",
        scopes=("database:connect",),
    )
    app = DatabricksAppSettings.from_values(
        trusted_ingress="databricks-apps",
        host="0.0.0.0",  # noqa: S104
        port=int(values["DATABRICKS_APP_PORT"]),
        path_prefix=values.get("TETHER_MOUNT_PATH", ""),
        shutdown_timeout_seconds=14.0,
        database_profile=profile,
        resource_bindings=(binding,),
    )
    api = ApiSettings(
        api_prefix=values.get("TETHER_API_PREFIX", "/api/v1"),
        allowed_origins=(values["TETHER_PUBLIC_ORIGIN"],),
    )
    ui = UiSettings()
    return app, api, ui


def lakebase_source(values: Mapping[str, str]) -> tuple[Actor, LakebaseConnectionSource]:
    """Create explicit app identity and request-scoped OAuth source values."""
    required = (
        "DATABRICKS_HOST",
        "TETHER_PG_ENDPOINT",
        "TETHER_PG_HOST",
        "TETHER_PG_DATABASE",
        "TETHER_PG_INFRA_ROLE",
    )
    if any(not values.get(name) for name in required):
        raise ValueError("required_database_settings_missing")
    infra_role = values["TETHER_PG_INFRA_ROLE"]
    settings = LakebaseSettings(
        workspace_host=values["DATABRICKS_HOST"],
        endpoint=values["TETHER_PG_ENDPOINT"],
        host=values["TETHER_PG_HOST"],
        database=values["TETHER_PG_DATABASE"],
        infra_role=infra_role,
        port=int(values.get("TETHER_PG_PORT", "5432")),
        sslrootcert=values.get("TETHER_PG_SSLROOTCERT"),
    )
    source = LakebaseConnectionSource(
        settings,
        database_user=lambda actor: actor.id,
        infrastructure_client=app_service_workspace_client(),
    )
    return Actor(id=infra_role, kind="service"), source


__all__ = ["deployment_settings", "lakebase_source"]
