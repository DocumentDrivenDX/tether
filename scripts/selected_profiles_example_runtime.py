"""Selected-schema startup and no-migration controls through the shipped example."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias, cast
from uuid import uuid4

from databricks.sdk import WorkspaceClient
from selected_profiles_example_lifespan import (
    cancel_and_join as _cancel_and_join,
)
from selected_profiles_example_lifespan import (
    next_message as _next_lifespan_message,
)
from selected_profiles_example_lifespan import run_thread as _run_thread
from selected_profiles_identity_credentials import workspace_for_bearer
from selected_schema_failure import missing_schema_refusal_code
from starlette.types import ASGIApp, Receive, Scope, Send
from tether_databricks import DatabricksResources, ResourceAcquirer, ResourceRef
from tether_databricks_example.postgres import (
    LakebaseConnectionSource,
    LakebaseSettings,
)
from tether_databricks_example.qualification import qualification_client_settings
from tether_databricks_example.runtime import (
    BorrowedSourceLease,
    RuntimeBindings,
    build_reference_app,
)
from tether_databricks_example.settings import deployment_settings
from tether_web_api import ApiFactory, ApiSettings, Authenticator, create_api
from tether_web_ui import UiFactory, UiSettings, create_static_app

from tether.client import Actor, ProvidedStorage
from tether.client.postgres import ConnectionSource

Snapshot: TypeAlias = tuple[
    tuple[tuple[object, ...], ...],
    tuple[object, ...],
    tuple[tuple[object, ...], ...],
    tuple[object, ...],
    tuple[object, ...],
    tuple[tuple[object, ...], ...],
    tuple[tuple[object, ...], ...],
]


def _missing_schema_refusal_code(error: BaseException) -> str | None:
    return missing_schema_refusal_code(error)


class _CountingResources(DatabricksResources):
    def __init__(self, bindings: tuple[ResourceRef, ...], acquire: ResourceAcquirer) -> None:
        super().__init__(bindings, acquire)
        self.close_calls = 0

    async def aclose(self) -> tuple[str, ...]:
        self.close_calls += 1
        return await super().aclose()


class _Factories:
    def __init__(self) -> None:
        self.api_calls = 0
        self.ui_calls = 0

    def api(self, *, clients, authenticator: Authenticator, settings: ApiSettings) -> ASGIApp:
        self.api_calls += 1
        return create_api(clients=clients, authenticator=authenticator, settings=settings)

    def ui(self, *, settings: UiSettings) -> ASGIApp:
        self.ui_calls += 1
        return create_static_app(settings=settings)


@dataclass(frozen=True, slots=True)
class _ConstructorInputs:
    workspace_host: str
    capability_profile: str
    listener_free_port: int


def _constructor_inputs(
    bundle: Path, values: Mapping[str, str], workspace_host: str
) -> _ConstructorInputs:
    from qualify_selected_profiles import locked_bundle

    locked_bundle(bundle)
    capability_profile = _bundle_capability_profile(bundle)
    configured_host = values.get("DATABRICKS_HOST")
    configured_capability = values.get("TETHER_PG_CAPABILITY_PROFILE")
    if configured_host is not None and configured_host != workspace_host:
        raise RuntimeError("selected_workspace_host_mismatch")
    if configured_capability is not None and configured_capability != capability_profile:
        raise RuntimeError("selected_bundle_capability_profile_mismatch")
    # Port 1 satisfies the public settings model. This control runs the ASGI
    # lifespan directly and never asks an OS listener to bind it.
    return _ConstructorInputs(workspace_host, capability_profile, 1)


def _bundle_capability_profile(bundle: Path) -> str:
    try:
        lines = (bundle / "app.yaml").read_text(encoding="utf-8").splitlines()
    except OSError:
        raise RuntimeError("selected_bundle_manifest_unavailable") from None
    found: list[str] = []
    for index, line in enumerate(lines[:-1]):
        if line.strip() != "- name: TETHER_PG_CAPABILITY_PROFILE":
            continue
        key, separator, encoded = lines[index + 1].strip().partition(":")
        if not separator or key != "value":
            raise RuntimeError("selected_bundle_capability_profile_invalid")
        raw = encoded.strip()
        try:
            value = json.loads(raw) if raw.startswith('"') else raw
        except json.JSONDecodeError:
            raise RuntimeError("selected_bundle_capability_profile_invalid") from None
        if (
            not isinstance(value, str)
            or not value
            or re.fullmatch(r"[A-Za-z0-9._-]+", value) is None
        ):
            raise RuntimeError("selected_bundle_capability_profile_invalid")
        found.append(value)
    if len(found) != 1:
        raise RuntimeError("selected_bundle_capability_profile_invalid")
    return found[0]


async def _resource_provider(
    settings,
    source: ConnectionSource,
) -> _CountingResources:
    async def acquire(ref: ResourceRef) -> BorrowedSourceLease:
        if ref not in settings.resource_bindings:
            raise PermissionError("resource_not_declared")
        return BorrowedSourceLease(source)

    return _CountingResources(settings.resource_bindings, cast(ResourceAcquirer, acquire))


async def _snapshot(source: ConnectionSource, actor: Actor, schema: str) -> Snapshot:
    async with source(actor, None) as connection:
        schema_cursor = await connection.execute(
            "SELECT nspname, nspowner::regrole::text, nspacl::text "
            "FROM pg_namespace WHERE nspname = %s",
            (schema,),
        )
        schema_rows = cast(Sequence[Sequence[object]], await schema_cursor.fetchall())
        role_cursor = await connection.execute(
            "SELECT oid, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolcanlogin "
            "FROM pg_roles WHERE rolname = %s",
            (actor.id,),
        )
        role_row = await role_cursor.fetchone()
        if role_row is None:
            raise RuntimeError("selected_database_role_missing")
        role = tuple(cast(Sequence[object], role_row))
        membership_cursor = await connection.execute(
            "SELECT member, roleid, admin_option, inherit_option, set_option "
            "FROM pg_auth_members WHERE member = %s OR roleid = %s ORDER BY member, roleid",
            (role[0], role[0]),
        )
        membership_rows = cast(Sequence[Sequence[object]], await membership_cursor.fetchall())
        database_cursor = await connection.execute(
            "SELECT has_database_privilege(%s, current_database(), 'CONNECT'), "
            "has_database_privilege(%s, current_database(), 'CREATE')",
            (actor.id, actor.id),
        )
        database_row = await database_cursor.fetchone()
        if database_row is None:
            raise RuntimeError("database_privilege_snapshot_missing")
        if schema_rows:
            schema_privilege_cursor = await connection.execute(
                "SELECT has_schema_privilege(%s, %s, 'USAGE'), "
                "has_schema_privilege(%s, %s, 'CREATE')",
                (actor.id, schema, actor.id, schema),
            )
            schema_privileges = await schema_privilege_cursor.fetchone()
            if schema_privileges is None:
                raise RuntimeError("schema_privilege_snapshot_missing")
        else:
            schema_privileges = ("schema_absent",)
        object_cursor = await connection.execute(
            "SELECT c.relname, c.relkind, c.relacl::text "
            "FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s ORDER BY c.relname LIMIT 1001",
            (schema,),
        )
        object_rows = cast(Sequence[Sequence[object]], await object_cursor.fetchall())
        if len(object_rows) > 1000:
            raise RuntimeError("schema_object_snapshot_exceeds_bound")
        table_grant_cursor = await connection.execute(
            "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee = %s AND table_schema = %s "
            "ORDER BY table_name, privilege_type LIMIT 1001",
            (actor.id, schema),
        )
        table_grants = cast(Sequence[Sequence[object]], await table_grant_cursor.fetchall())
        if len(table_grants) > 1000:
            raise RuntimeError("schema_grant_snapshot_exceeds_bound")
    return cast(
        Snapshot,
        (
            tuple(tuple(row) for row in schema_rows),
            role,
            tuple(tuple(row) for row in membership_rows),
            tuple(cast(Sequence[object], database_row)),
            tuple(cast(Sequence[object], schema_privileges)),
            tuple(tuple(row) for row in object_rows),
            tuple(tuple(row) for row in table_grants),
        ),
    )


async def _lifespan(app: ASGIApp) -> Callable[[], Awaitable[None]]:
    incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    outgoing: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def receive() -> dict[str, Any]:
        return await incoming.get()

    async def send(message: dict[str, Any]) -> None:
        await outgoing.put(message)

    scope: Scope = cast(
        Scope,
        {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.0"}},
    )
    receive_port, send_port = cast(Receive, receive), cast(Send, send)
    task = asyncio.ensure_future(app(scope, receive_port, send_port))
    try:
        await incoming.put({"type": "lifespan.startup"})
        startup = await _next_lifespan_message(
            outgoing, task, "reference_runtime_startup_not_observed"
        )
        if startup.get("type") != "lifespan.startup.complete":
            raise RuntimeError("reference_runtime_startup_failed")
    except BaseException:
        await _cancel_and_join(task)
        raise

    async def shutdown() -> None:
        try:
            await incoming.put({"type": "lifespan.shutdown"})
            result = await _next_lifespan_message(
                outgoing, task, "reference_runtime_shutdown_not_observed"
            )
            if result.get("type") != "lifespan.shutdown.complete":
                raise RuntimeError("reference_runtime_shutdown_failed")
            await task
        finally:
            await _cancel_and_join(task)

    return shutdown


def _bindings_settings(
    values: Mapping[str, str],
    *,
    schema: str,
    actor: Actor,
    constructor: _ConstructorInputs,
):
    runtime_values = dict(values)
    runtime_values["TETHER_PG_SCHEMA"] = schema
    runtime_values["DATABRICKS_HOST"] = constructor.workspace_host
    runtime_values["TETHER_PG_CAPABILITY_PROFILE"] = constructor.capability_profile
    # Used only by the in-process lifespan control. No listener is opened and
    # this value is never treated as the platform-assigned app port.
    runtime_values["DATABRICKS_APP_PORT"] = str(constructor.listener_free_port)
    app_settings, api_settings, ui_settings = deployment_settings(runtime_values)
    profile = app_settings.database_profile
    if profile is None:
        raise RuntimeError("durable_database_profile_required")
    # This qualification constructor control opens as the selected nonadmin
    # caller. It does not claim to possess or exercise the deployed app SP token.
    copied_profile = profile.model_copy(update={"migration_owner": actor.id})
    return (
        runtime_values,
        app_settings.model_copy(update={"database_profile": copied_profile}),
        api_settings,
        ui_settings,
    )


async def _build(
    values: Mapping[str, str],
    *,
    schema: str,
    actor: Actor,
    source: ConnectionSource,
    factories: _Factories,
    constructor: _ConstructorInputs,
    provider: _CountingResources | None = None,
):
    _runtime_values, app_settings, api_settings, ui_settings = _bindings_settings(
        values, schema=schema, actor=actor, constructor=constructor
    )
    if provider is None:
        provider = await _resource_provider(app_settings, source)
    bindings = RuntimeBindings(
        settings=app_settings,
        client_settings=lambda storage: qualification_client_settings(
            cast(ProvidedStorage, storage)
        ),
        infrastructure_actor=actor,
        resource_provider=provider,
        database_binding=app_settings.resource_bindings[0],
        authenticator=None,
        api_settings=api_settings,
        ui_settings=ui_settings,
        api_factory=cast(ApiFactory, factories.api),
        ui_factory=cast(UiFactory, factories.ui),
    )
    app = await build_reference_app(
        bindings,
        api_factory=bindings.api_factory,
        ui_factory=bindings.ui_factory,
    )
    return app, provider, factories


async def selected_example_schema_refusal(
    *,
    workspace: WorkspaceClient,
    values: dict[str, str],
    selected_schema: str,
    bundle: Path,
) -> None:
    """@covers US-068-AC7: example startup succeeds, then refuses missing schema."""
    actor, source = await _selected_nonadmin_source(workspace, values)
    workspace_host = cast(str, workspace.config.host)
    constructor = await _run_thread(_constructor_inputs, bundle, values, workspace_host)
    before = await _check_existing_schema(source, actor, selected_schema)
    await _check_good_example_startup(values, selected_schema, actor, source, constructor)
    missing_schema = "fabricated_missing_" + uuid4().hex[:20]
    missing_before = await _snapshot(source, actor, missing_schema)
    if missing_before[0]:
        raise RuntimeError("missing_schema_probe_not_isolated")
    await _check_missing_schema_refusal(values, missing_schema, actor, source, constructor)
    after = await _snapshot(source, actor, selected_schema)
    missing_after = await _snapshot(source, actor, missing_schema)
    if before != after or missing_before != missing_after:
        raise RuntimeError("missing_schema_changed_role_or_schema_metadata")


async def _selected_nonadmin_source(
    workspace: WorkspaceClient, values: Mapping[str, str]
) -> tuple[Actor, LakebaseConnectionSource]:
    identity = await _run_thread(workspace.current_user.me)
    headers = await _run_thread(workspace.config.authenticate)
    username = getattr(identity, "user_name", None)
    authorization = headers.get("Authorization") or headers.get("authorization")
    service_role = values.get("TETHER_SERVICE_CALLER_DATABASE_ROLE", "")
    app_role = values.get("TETHER_PG_INFRA_ROLE", "")
    workspace_host = getattr(workspace.config, "host", None)
    if not isinstance(workspace_host, str) or not workspace_host:
        raise RuntimeError("selected_workspace_host_unavailable")
    if "DATABRICKS_HOST" in values and values["DATABRICKS_HOST"] != workspace_host:
        raise RuntimeError("selected_workspace_host_mismatch")
    if (
        not isinstance(username, str)
        or not username
        or not isinstance(authorization, str)
        or not authorization.startswith("Bearer ")
        or not service_role
        or username != service_role
        or not app_role
        or service_role == app_role
    ):
        raise RuntimeError("selected_nonadmin_actor_role_mismatch")
    actor = Actor(id=username, credential=_StaticCredential(authorization))
    source = LakebaseConnectionSource(
        LakebaseSettings(
            workspace_host=workspace_host,
            endpoint=values["TETHER_PG_ENDPOINT"],
            host=values["TETHER_PG_HOST"],
            database=values["TETHER_PG_DATABASE"],
            infra_role=app_role,
            port=int(values.get("TETHER_PG_PORT", "5432")),
            sslrootcert=values.get("TETHER_PG_SSLROOTCERT"),
        ),
        database_user=lambda current_actor: current_actor.id,
        infrastructure_client=workspace,
        user_client_factory=workspace_for_bearer,
    )
    return actor, source


async def _check_existing_schema(source: ConnectionSource, actor: Actor, schema: str) -> Snapshot:
    async with source(actor, None) as connection:
        cursor = await connection.execute("SELECT 1")
        if await cursor.fetchone() != (1,):
            raise RuntimeError("valid_database_baseline_failed")
    snapshot = await _snapshot(source, actor, schema)
    if not snapshot[0] or snapshot[1][1] or snapshot[1][2]:
        raise RuntimeError("selected_schema_or_nonadmin_role_invalid")
    return snapshot


async def _check_good_example_startup(
    values: Mapping[str, str],
    schema: str,
    actor: Actor,
    source: ConnectionSource,
    constructor: _ConstructorInputs,
) -> None:
    factories = _Factories()
    app, provider, _ = await _build(
        values,
        schema=schema,
        actor=actor,
        source=source,
        factories=factories,
        constructor=constructor,
    )
    stop = await _lifespan(cast(ASGIApp, app))
    await stop()
    report = app.shutdown_report
    if factories.api_calls != 1 or factories.ui_calls != 1:
        raise RuntimeError("selected_example_factories_not_published")
    if provider.close_calls != 1 or report is None or not report.complete:
        raise RuntimeError("selected_example_startup_or_close_incomplete")


async def _check_missing_schema_refusal(
    values: Mapping[str, str],
    schema: str,
    actor: Actor,
    source: ConnectionSource,
    constructor: _ConstructorInputs,
) -> None:
    factories = _Factories()
    _runtime_values, app_settings, _api_settings, _ui_settings = _bindings_settings(
        values, schema=schema, actor=actor, constructor=constructor
    )
    provider = await _resource_provider(app_settings, source)
    try:
        app, _opened_provider, _ = await _build(
            values,
            schema=schema,
            actor=actor,
            source=source,
            factories=factories,
            constructor=constructor,
            provider=provider,
        )
    except Exception as error:
        if _missing_schema_refusal_code(error) is None:
            raise RuntimeError("missing_schema_refusal_unclassified") from None
    else:
        stop = await _lifespan(cast(ASGIApp, app))
        await stop()
        raise RuntimeError("missing_schema_open_unexpectedly_succeeded")
    if provider.close_calls != 1 or factories.api_calls or factories.ui_calls:
        raise RuntimeError("missing_schema_published_factories_or_leaked_lease")


class _StaticCredential:
    def __init__(self, value: str) -> None:
        self._value = value

    async def authorization(self) -> str:
        return self._value


__all__ = ["selected_example_schema_refusal"]
