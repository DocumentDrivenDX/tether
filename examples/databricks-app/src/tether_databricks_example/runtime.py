"""Explicit construction of one shared Tether host for a Databricks app."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import cast

from tether_databricks import (
    CarrierASGI,
    DatabaseProfileRef,
    DatabricksAppSettings,
    DatabricksIdentityResolver,
    DatabricksResources,
    ResourceAcquirer,
    ResourceLifecycle,
    ResourceRef,
    ServiceCallerAuthenticator,
    create_carrier_app,
)
from tether_web_api import ApiFactory, ApiSettings, Authenticator
from tether_web_ui import UiFactory, UiSettings

from tether.client import Actor, ClientHost, ClientSettings, ProvidedStorage
from tether.client.databricks import SERVICE_CALLER_CREDENTIAL_HEADER
from tether.client.postgres import (
    ConnectionSource,
    PostgresDurabilityProfile,
    PostgresResources,
)


@dataclass(frozen=True)
class RuntimeBindings:
    """All deployment-specific authorities are supplied by the embedding app."""

    settings: DatabricksAppSettings
    client_settings: Callable[[ProvidedStorage], ClientSettings]
    infrastructure_actor: Actor
    resource_provider: DatabricksResources
    database_binding: ResourceRef
    authenticator: Authenticator | None
    api_settings: ApiSettings
    ui_settings: UiSettings
    api_factory: ApiFactory
    ui_factory: UiFactory
    service_caller_authenticator: ServiceCallerAuthenticator | None = None


class _HostClients:
    def __init__(self, host: ClientHost) -> None:
        self._host = host

    def __call__(self, actor: Actor):
        return self._host.client(actor)


class BorrowedSourceLease:
    """An example resource lease for an embedder-owned connection source."""

    owned = False

    def __init__(self, value: object) -> None:
        self.value = value

    async def close(self) -> None:
        return None


def resource_provider_for_source(
    settings: DatabricksAppSettings, source: ConnectionSource
) -> DatabricksResources:
    """Bind a caller-owned OAuth connection source to the declared DB resource."""

    async def acquire(ref: ResourceRef) -> BorrowedSourceLease:
        if ref not in settings.resource_bindings:
            raise PermissionError("resource_not_declared")
        return BorrowedSourceLease(source)

    return DatabricksResources.from_bindings(
        settings.resource_bindings,
        acquire=cast(ResourceAcquirer, acquire),
    )


def _database_profile(bindings: RuntimeBindings) -> DatabaseProfileRef:
    profile = bindings.settings.database_profile
    if profile is None:
        raise ValueError("durable_database_profile_required")
    if bindings.database_binding.name != profile.source_id:
        raise ValueError("database_resource_profile_mismatch")
    return profile


async def _open_postgres(
    bindings: RuntimeBindings, profile: DatabaseProfileRef
) -> PostgresResources:
    lease = await bindings.resource_provider.acquire(bindings.database_binding)
    if not callable(lease.value):
        raise TypeError("database_resource_must_be_connection_source")
    return await PostgresResources.open(
        cast(ConnectionSource, lease.value),
        actor=bindings.infrastructure_actor,
        schema=profile.schema_name,
        durability_profile=PostgresDurabilityProfile(
            profile.durability_profile,
            endpoint_id=profile.source_id
            if profile.durability_profile == "databricks-lakebase-pg18"
            else None,
        ),
    )


async def _cleanup_failed_construction(
    bindings: RuntimeBindings,
    lifecycle: ResourceLifecycle,
) -> None:
    with suppress(Exception):
        await lifecycle.close(timeout_seconds=bindings.settings.shutdown_timeout_seconds)


async def build_reference_app(
    bindings: RuntimeBindings,
    *,
    api_factory: ApiFactory,
    ui_factory: UiFactory,
) -> CarrierASGI:
    """Open only supplied durable resources and compose the public API/UI factories."""
    opened: dict[str, object | None] = {"postgres": None, "host": None}

    async def close_host() -> bool:
        host = opened["host"]
        if isinstance(host, ClientHost):
            await host.aclose()
            return await host.wait_closed()
        return True

    async def close_postgres() -> bool:
        postgres = opened["postgres"]
        if isinstance(postgres, PostgresResources):
            await postgres.aclose()
            return await postgres.wait_closed()
        return True

    async def close_platform_resources() -> bool:
        return not await bindings.resource_provider.aclose()

    # Register the dependency chain before acquisition: even a failed open can
    # leave an acquired platform lease that must outlive its database users.
    lifecycle = ResourceLifecycle(
        {
            "client_host": close_host,
            "postgres_root": close_postgres,
            "platform_resources": close_platform_resources,
        }
    )
    try:
        profile = _database_profile(bindings)
        postgres = await _open_postgres(bindings, profile)
        opened["postgres"] = postgres
        client_settings = bindings.client_settings(postgres.storage)
        if client_settings.storage != postgres.storage or client_settings.resume != "off":
            raise ValueError("provided_storage_requires_resume_off")
        host = await ClientHost.open(client_settings)
        opened["host"] = host

        async def prepare_actor(actor: Actor) -> None:
            await postgres.recover_owner(actor)

        identity = DatabricksIdentityResolver(
            trusted_ingress=bindings.settings.trusted_ingress,
            local_authenticator=bindings.authenticator,
            prepare_actor=prepare_actor,
            service_caller_authenticator=bindings.service_caller_authenticator,
            service_caller_credential_header=(
                SERVICE_CALLER_CREDENTIAL_HEADER.lower()
                if bindings.service_caller_authenticator is not None
                else None
            ),
        )

        return cast(
            CarrierASGI,
            create_carrier_app(
                settings=bindings.settings,
                client_provider=_HostClients(host),
                authenticator=identity,
                resource_provider=bindings.resource_provider,
                api_factory=api_factory,
                api_settings=bindings.api_settings,
                ui_factory=ui_factory,
                ui_settings=bindings.ui_settings,
                lifecycle=lifecycle,
            ),
        )
    except BaseException:
        await _cleanup_failed_construction(bindings, lifecycle)
        raise


__all__ = ["RuntimeBindings", "build_reference_app"]
