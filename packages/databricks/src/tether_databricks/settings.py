"""Explicit, non-secret Databricks Apps configuration values."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Literal, cast

from pydantic import Field, field_validator, model_validator

from tether.client import Payload

from .resources import ResourceRef

TrustedIngress = Literal["databricks-apps", "local"]


class CarrierConfigurationError(ValueError):
    """A required platform or resource setting is absent or invalid."""


class DatabaseProfileRef(Payload):
    """Non-secret identity of an externally configured database profile."""

    source_id: str = Field(min_length=1, max_length=128)
    schema_name: str = Field(alias="schema", min_length=1, max_length=63)
    role_mode: Literal["caller", "service", "rls"]
    tls_mode: Literal["required", "local-socket"]
    durability_profile: Literal["native", "databricks-lakebase-pg18"] = "native"
    migration_owner: str = Field(min_length=1, max_length=128)
    capability_profile: str = Field(min_length=1, max_length=128)

    @field_validator("source_id", "schema_name", "migration_owner", "capability_profile")
    @classmethod
    def plain_identifier(cls, value: str) -> str:
        if not value.isascii() or any(ord(char) < 32 for char in value):
            raise ValueError("invalid_database_profile")
        return value


class DatabricksAppSettings(Payload):
    """Carrier settings. Values are supplied explicitly; this type reads no environment."""

    trusted_ingress: TrustedIngress
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    path_prefix: str = ""
    shutdown_timeout_seconds: float = Field(gt=0, lt=15, allow_inf_nan=False)
    database_profile: DatabaseProfileRef | None = None
    resource_bindings: tuple[ResourceRef, ...]

    @field_validator("path_prefix")
    @classmethod
    def normalized_prefix(cls, value: str) -> str:
        if value in {"", "/"}:
            return ""
        if (
            not value.startswith("/")
            or value.endswith("/")
            or any(part in {"", ".", ".."} for part in value[1:].split("/"))
            or any(char in value for char in ("?", "#", "\\"))
        ):
            raise ValueError("invalid_path_prefix")
        return value

    @model_validator(mode="after")
    def host_is_explicit(self) -> DatabricksAppSettings:
        if self.trusted_ingress == "databricks-apps" and self.host != "0.0.0.0":  # noqa: S104
            raise ValueError("apps_host_must_bind_all_interfaces")
        return self

    @classmethod
    def from_values(
        cls,
        *,
        trusted_ingress: TrustedIngress,
        host: str,
        port: int,
        path_prefix: str = "",
        shutdown_timeout_seconds: float,
        database_profile: DatabaseProfileRef | None = None,
        resource_bindings: tuple[ResourceRef, ...],
    ) -> DatabricksAppSettings:
        return cls(
            trusted_ingress=trusted_ingress,
            host=host,
            port=port,
            path_prefix=path_prefix,
            shutdown_timeout_seconds=shutdown_timeout_seconds,
            database_profile=database_profile,
            resource_bindings=resource_bindings,
        )

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, str],
        *,
        trusted_ingress: TrustedIngress,
    ) -> DatabricksAppSettings:
        """Map explicitly supplied environment-like values without reading process state."""
        allowed = {
            "DATABRICKS_APP_HOST",
            "DATABRICKS_APP_PORT",
            "TETHER_PORT",
            "TETHER_PATH_PREFIX",
            "TETHER_SHUTDOWN_TIMEOUT_SECONDS",
            "TETHER_DATABASE_SOURCE_ID",
            "TETHER_DATABASE_SCHEMA",
            "TETHER_DATABASE_ROLE_MODE",
            "TETHER_DATABASE_TLS_MODE",
            "TETHER_DATABASE_DURABILITY_PROFILE",
            "TETHER_DATABASE_MIGRATION_OWNER",
            "TETHER_DATABASE_CAPABILITY_PROFILE",
            "TETHER_RESOURCE_BINDINGS",
        }
        for key in values:
            if re.search(r"(?i)(token|password|secret|credential|connection|string|dsn)", key):
                raise CarrierConfigurationError("secret_settings_must_be_injected_separately")
            if key not in allowed:
                raise CarrierConfigurationError("unknown_app_setting")

        host = values.get("DATABRICKS_APP_HOST", "0.0.0.0")  # noqa: S104
        port_value = values.get("DATABRICKS_APP_PORT", values.get("TETHER_PORT"))
        timeout_value = values.get("TETHER_SHUTDOWN_TIMEOUT_SECONDS")
        if port_value is None or timeout_value is None:
            raise CarrierConfigurationError("required_app_settings_missing")
        try:
            port = int(port_value)
            timeout = float(timeout_value)
            bindings_value = values.get("TETHER_RESOURCE_BINDINGS", "[]")
            raw_bindings: object = json.loads(bindings_value)
            if not isinstance(raw_bindings, list):
                raise ValueError
            bindings = tuple(
                ResourceRef.model_validate(item) for item in cast(list[object], raw_bindings)
            )
            profile = _profile(values)
        except (TypeError, ValueError):
            raise CarrierConfigurationError("invalid_app_settings") from None
        return cls.from_values(
            trusted_ingress=trusted_ingress,
            host=host,
            port=port,
            path_prefix=values.get("TETHER_PATH_PREFIX", ""),
            shutdown_timeout_seconds=timeout,
            database_profile=profile,
            resource_bindings=bindings,
        )


def _profile(values: Mapping[str, str]) -> DatabaseProfileRef | None:
    fields = {
        "source_id": values.get("TETHER_DATABASE_SOURCE_ID"),
        "schema": values.get("TETHER_DATABASE_SCHEMA"),
        "role_mode": values.get("TETHER_DATABASE_ROLE_MODE"),
        "tls_mode": values.get("TETHER_DATABASE_TLS_MODE"),
        "durability_profile": values.get("TETHER_DATABASE_DURABILITY_PROFILE", "native"),
        "migration_owner": values.get("TETHER_DATABASE_MIGRATION_OWNER"),
        "capability_profile": values.get("TETHER_DATABASE_CAPABILITY_PROFILE"),
    }
    present = [value is not None for value in fields.values()]
    if not any(present):
        return None
    if not all(present):
        raise CarrierConfigurationError("incomplete_database_profile")
    return DatabaseProfileRef.model_validate(fields)


__all__ = [
    "CarrierConfigurationError",
    "DatabaseProfileRef",
    "DatabricksAppSettings",
    "TrustedIngress",
]
