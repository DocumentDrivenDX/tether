"""Offline controller checks for the selected example-startup AC7 proof."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
import selected_profiles_example_runtime as example_runtime
import selected_profiles_process
from context_lifetime_controls import wait_event_or_task
from databricks.sdk import WorkspaceClient
from process_lifetime_controls import ShieldCancellationObserver
from public_port_fixtures import RecordingModel, RecordingPolicy
from qualify_live_apps_incomplete import LiveQualificationIncomplete
from selected_profiles_example_lifespan import run_thread
from selected_profiles_example_runtime import (
    _bindings_settings,
    _constructor_inputs,
    _ConstructorInputs,
    _missing_schema_refusal_code,
)
from tether_databricks import (
    DatabaseProfileRef,
    DatabricksAppSettings,
    DatabricksResources,
    ResourceAcquirer,
    ResourceRef,
)
from tether_databricks_example.runtime import RuntimeBindings, build_reference_app
from tether_web_api import ApiFactory, ApiSettings
from tether_web_ui import UiFactory, UiSettings

from tether.client import (
    Actor,
    ClientLimits,
    ClientSettings,
    IntegrityError,
    ModelExecutionLimits,
    ModelResponse,
    ProvidedStorage,
)
from tether.client.postgres import ConnectionSource, PostgresResources
from tether.client.testing import fabricated_model_context, scripted_model


def test_example_runtime_thread_work_is_retained_through_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    observer = ShieldCancellationObserver(2)
    observer.install(selected_profiles_process, monkeypatch)

    async def run() -> None:
        entered = asyncio.Event()
        finished = asyncio.Event()
        loop = asyncio.get_running_loop()

        def operation() -> str:
            loop.call_soon_threadsafe(entered.set)
            release.wait()
            loop.call_soon_threadsafe(finished.set)
            return "settled"

        task = asyncio.create_task(run_thread(operation))
        observer.target = task
        try:
            await wait_event_or_task(entered, task)
            task.cancel()
            await wait_event_or_task(observer.acknowledged[0], task)
            task.cancel()
            await wait_event_or_task(observer.acknowledged[1], task)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert finished.is_set()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


class _SqlStateError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__("fabricated")
        self.sqlstate = sqlstate


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (IntegrityError("database_schema_mismatch"), "database_schema_mismatch"),
        (IntegrityError("database_connection_unavailable"), None),
        (_SqlStateError("3F000"), "3F000"),
        (_SqlStateError("42P01"), "42P01"),
        (_SqlStateError("28P01"), None),
        (RuntimeError("permission denied"), None),
    ],
)
def test_ac7_classifier_accepts_only_typed_missing_schema_failures(error, expected):
    assert _missing_schema_refusal_code(error) == expected


class _OwnedLease:
    owned = True

    def __init__(self, value: object) -> None:
        self.value = value
        self.close_count = 0

    async def close(self) -> None:
        self.close_count += 1


@pytest.mark.anyio
async def test_example_startup_refusal_closes_lease_before_framework_factories(monkeypatch):
    ref = ResourceRef(name="fabricated-endpoint", kind="lakebase-postgres")
    settings = DatabricksAppSettings.from_values(
        trusted_ingress="databricks-apps",
        host="0.0.0.0",  # noqa: S104
        port=8000,
        shutdown_timeout_seconds=3,
        database_profile=DatabaseProfileRef(
            source_id=ref.name,
            schema="fabricated_missing",
            role_mode="caller",
            tls_mode="required",
            durability_profile="databricks-lakebase-pg18",
            migration_owner="fabricated-owner",
            capability_profile="fabricated-capabilities",
        ),
        resource_bindings=(ref,),
    )
    lease = _OwnedLease(lambda *_args: None)

    async def acquire(_ref: ResourceRef) -> _OwnedLease:
        return lease

    resources = DatabricksResources.from_bindings((ref,), acquire=cast(ResourceAcquirer, acquire))
    factories = {"api": 0, "ui": 0}

    async def fail_open(*_args, **_kwargs):
        raise IntegrityError("database_schema_mismatch")

    monkeypatch.setattr(PostgresResources, "open", classmethod(fail_open))

    def api_factory(**_kwargs):
        factories["api"] += 1
        raise AssertionError("API factory ran before database validation")

    def ui_factory(**_kwargs):
        factories["ui"] += 1
        raise AssertionError("UI factory ran before database validation")

    context = fabricated_model_context()

    def client_settings(
        storage: ProvidedStorage,
        *,
        client_limits: ClientLimits,
        model_limits: ModelExecutionLimits,
    ) -> ClientSettings:
        return ClientSettings(
            model=RecordingModel(
                scripted_model([ModelResponse(content="fabricated", finish_reason="stop")])
            ),
            storage=storage,
            policy=RecordingPolicy(),
            client_limits=client_limits,
            model_limits=model_limits,
            context_config=context["context_config"],
            estimator=context["estimator"],
            specs=(),
            resume="off",
        )

    client_limits = ClientLimits(
        max_page_size=100,
        max_call_ids=8,
        max_read_bytes=1024,
        max_lookup_entries=100,
        read_timeout_seconds=2,
        heartbeat_interval_seconds=0.1,
        shutdown_timeout_seconds=2,
        follower_queue_size=8,
        max_followers=8,
        max_active_turns=8,
        max_request_bytes=4096,
    )
    model_limits = ModelExecutionLimits(
        max_response_bytes=8192,
        max_tool_argument_bytes=512,
        max_attempts=1,
        request_timeout_seconds=2,
        max_retry_delay_seconds=0.1,
    )

    def client_settings_for_ports(storage: ProvidedStorage) -> ClientSettings:
        return client_settings(storage, client_limits=client_limits, model_limits=model_limits)

    bindings = RuntimeBindings(
        settings=settings,
        client_settings=client_settings_for_ports,
        infrastructure_actor=Actor(id="fabricated-owner"),
        resource_provider=resources,
        database_binding=ref,
        authenticator=None,
        api_settings=ApiSettings(allowed_origins=("https://fabricated.example",)),
        ui_settings=UiSettings(),
        api_factory=cast(ApiFactory, api_factory),
        ui_factory=cast(UiFactory, ui_factory),
    )
    with pytest.raises(IntegrityError, match="database_schema_mismatch"):
        await build_reference_app(
            bindings,
            api_factory=bindings.api_factory,
            ui_factory=bindings.ui_factory,
        )
    assert factories == {"api": 0, "ui": 0}
    assert lease.close_count == 1
    assert await resources.aclose() == ()


class _Cursor:
    def __init__(self, row=None, rows=()):
        self._row = row
        self._rows = rows

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return self._rows


class _SnapshotConnection:
    def __init__(self, *, schema_present: bool) -> None:
        self.schema_present = schema_present
        self.queries: list[str] = []

    async def execute(self, query: str, _params=()):
        self.queries.append(query)
        if "FROM pg_namespace" in query:
            rows = [("fabricated_schema", "fabricated_owner", "ACL")] if self.schema_present else []
            return _Cursor(rows=rows)
        if "FROM pg_roles" in query:
            return _Cursor(row=(42, False, False, False, False, True))
        if "FROM pg_auth_members" in query:
            return _Cursor(rows=[])
        if "has_database_privilege" in query:
            return _Cursor(row=(True, False))
        if "has_schema_privilege" in query:
            if not self.schema_present:
                raise AssertionError("schema privilege lookup used for an absent schema")
            return _Cursor(row=(True, False))
        if "FROM pg_class" in query or "information_schema.role_table_grants" in query:
            return _Cursor(rows=[])
        raise AssertionError("unrecognized fabricated catalog query")


class _SnapshotSource:
    def __init__(self, connection: _SnapshotConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def __call__(self, _actor, _session):
        yield self.connection


@pytest.mark.parametrize("schema_present", [False, True], ids=["absent", "present"])
@pytest.mark.anyio
async def test_snapshot_uses_name_based_schema_privileges_only_when_schema_exists(schema_present):
    connection = _SnapshotConnection(schema_present=schema_present)
    snapshot = await example_runtime._snapshot(
        cast(ConnectionSource, _SnapshotSource(connection)),
        Actor(id="fabricated-role"),
        "fabricated_schema",
    )
    assert ("has_schema_privilege" in " ".join(connection.queries)) is schema_present
    if schema_present:
        assert snapshot[4] == (True, False)
    else:
        assert snapshot[4] == ("schema_absent",)


@pytest.mark.anyio
async def test_selected_nonadmin_source_uses_explicit_sdk_host_without_mutating_values(monkeypatch):
    values = {
        "TETHER_SERVICE_CALLER_DATABASE_ROLE": "fabricated-caller-role",
        "TETHER_PG_INFRA_ROLE": "fabricated-infrastructure-role",
        "TETHER_PG_ENDPOINT": "fabricated-endpoint",
        "TETHER_PG_HOST": "database.fabricated.example",
        "TETHER_PG_DATABASE": "fabricated-database",
    }
    original = dict(values)
    workspace = SimpleNamespace(
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(user_name="fabricated-caller-role")
        ),
        config=SimpleNamespace(
            host="https://workspace.fabricated.example",
            authenticate=lambda: {"Authorization": " ".join(("Bearer", "fabricated-token"))},
        ),
    )
    created: dict[str, object] = {}

    def source_factory(settings, *, database_user, infrastructure_client, user_client_factory):
        created["settings"] = settings
        created["database_user"] = database_user
        created["infrastructure_client"] = infrastructure_client
        created["user_client_factory"] = user_client_factory
        return "fabricated-source"

    monkeypatch.setattr(example_runtime, "LakebaseConnectionSource", source_factory)
    actor, source = await example_runtime._selected_nonadmin_source(
        cast(WorkspaceClient, workspace), values
    )

    assert actor.id == "fabricated-caller-role"
    assert source == "fabricated-source"
    settings = cast(example_runtime.LakebaseSettings, created["settings"])
    database_user = cast(Callable[[Actor], str], created["database_user"])
    assert settings.workspace_host == "https://workspace.fabricated.example"
    assert database_user(actor) == actor.id
    assert created["infrastructure_client"] is workspace
    assert created["user_client_factory"] is example_runtime.workspace_for_bearer
    assert values == original


def test_constructor_inputs_require_locked_bundle_profile_and_explicit_host(
    monkeypatch, tmp_path: Path
):
    bundle = tmp_path / "selected-bundle"
    bundle.mkdir()
    (bundle / "app.yaml").write_text(
        'env:\n  - name: TETHER_PG_CAPABILITY_PROFILE\n    value: "fabricated-profile"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("qualify_selected_profiles.locked_bundle", lambda _bundle: None)
    source_values = {
        "DATABRICKS_HOST": "https://workspace.fabricated.example",
        "DATABRICKS_APP_PORT": "43127",
        "TETHER_PG_CAPABILITY_PROFILE": "fabricated-profile",
        "TETHER_PUBLIC_ORIGIN": "https://app.fabricated.example",
        "TETHER_PG_HOST": "database.fabricated.example",
        "TETHER_PG_DATABASE": "fabricated_database",
        "TETHER_PG_SCHEMA": "fabricated_schema",
        "TETHER_PG_ENDPOINT": "fabricated_endpoint",
        "TETHER_PG_INFRA_ROLE": "fabricated_infra",
    }
    constructor = _constructor_inputs(bundle, source_values, "https://workspace.fabricated.example")
    assert constructor.workspace_host == "https://workspace.fabricated.example"
    assert constructor.capability_profile == "fabricated-profile"
    assert constructor.listener_free_port == 1
    runtime_values, app_settings, _api, _ui = _bindings_settings(
        source_values,
        schema="fabricated_schema",
        actor=Actor(id="fabricated_actor"),
        constructor=constructor,
    )
    assert runtime_values["DATABRICKS_APP_PORT"] == "1"
    assert app_settings.port == 1
    assert source_values["DATABRICKS_APP_PORT"] == "43127"


@pytest.mark.parametrize(
    ("configured", "host", "expected"),
    [
        (
            {"DATABRICKS_HOST": "https://other.fabricated.example"},
            "https://workspace.fabricated.example",
            "selected_workspace_host_mismatch",
        ),
        (
            {"TETHER_PG_CAPABILITY_PROFILE": "fabricated-other"},
            "https://workspace.fabricated.example",
            "selected_bundle_capability_profile_mismatch",
        ),
    ],
)
def test_constructor_inputs_reject_settings_that_disagree_with_verified_bundle(
    monkeypatch, tmp_path: Path, configured: dict[str, str], host: str, expected: str
):
    bundle = tmp_path / "selected-bundle"
    bundle.mkdir()
    (bundle / "app.yaml").write_text(
        'env:\n  - name: TETHER_PG_CAPABILITY_PROFILE\n    value: "fabricated-profile"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("qualify_selected_profiles.locked_bundle", lambda _bundle: None)
    with pytest.raises(RuntimeError, match=expected):
        _constructor_inputs(bundle, configured, host)


@pytest.mark.anyio
async def test_constructor_settings_copy_source_values_and_use_listener_free_port():
    values = {
        "TETHER_PUBLIC_ORIGIN": "https://app.fabricated.example",
        "TETHER_PG_HOST": "database.fabricated.example",
        "TETHER_PG_DATABASE": "fabricated_database",
        "TETHER_PG_SCHEMA": "fabricated_schema",
        "TETHER_PG_ENDPOINT": "fabricated_endpoint",
        "TETHER_PG_INFRA_ROLE": "fabricated_infra",
    }
    original = dict(values)
    constructor = _ConstructorInputs(
        workspace_host="https://workspace.fabricated.example",
        capability_profile="fabricated_capabilities",
        listener_free_port=43127,
    )
    runtime_values, app_settings, _api_settings, _ui_settings = _bindings_settings(
        values,
        schema="fabricated_schema",
        actor=Actor(id="fabricated_caller"),
        constructor=constructor,
    )
    assert runtime_values["DATABRICKS_APP_PORT"] == "43127"
    assert runtime_values["DATABRICKS_HOST"] == constructor.workspace_host
    assert app_settings.port == 43127
    assert values == original


@pytest.mark.anyio
async def test_selected_nonadmin_source_rejects_conflicting_explicit_host(monkeypatch):
    values = {
        "DATABRICKS_HOST": "https://conflicting.fabricated.example",
        "TETHER_SERVICE_CALLER_DATABASE_ROLE": "fabricated-caller-role",
        "TETHER_PG_INFRA_ROLE": "fabricated-infrastructure-role",
        "TETHER_PG_ENDPOINT": "fabricated-endpoint",
        "TETHER_PG_HOST": "database.fabricated.example",
        "TETHER_PG_DATABASE": "fabricated-database",
    }
    workspace = SimpleNamespace(
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(user_name="fabricated-caller-role")
        ),
        config=SimpleNamespace(
            host="https://workspace.fabricated.example",
            authenticate=lambda: {"Authorization": " ".join(("Bearer", "fabricated-token"))},
        ),
    )
    monkeypatch.setattr(
        example_runtime,
        "LakebaseConnectionSource",
        lambda *_args, **_kwargs: pytest.fail("conflicting host reached source construction"),
    )

    with pytest.raises(RuntimeError, match="selected_workspace_host_mismatch"):
        await example_runtime._selected_nonadmin_source(cast(WorkspaceClient, workspace), values)


@pytest.mark.anyio
async def test_lifespan_startup_failure_cancels_and_joins_asgi_task():
    cleaned = asyncio.Event()

    async def refusing_app(_scope, receive, send):
        await receive()
        await send({"type": "lifespan.startup.failed", "message": "fabricated"})
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    with pytest.raises(RuntimeError, match="reference_runtime_startup_failed"):
        await example_runtime._lifespan(refusing_app)
    assert cleaned.is_set()


@pytest.mark.anyio
async def test_lifespan_waits_for_startup_and_shutdown_state_events():
    startup_entered = asyncio.Event()
    release_startup = asyncio.Event()
    shutdown_entered = asyncio.Event()
    release_shutdown = asyncio.Event()

    async def held_app(_scope, receive, send):
        await receive()
        startup_entered.set()
        await release_startup.wait()
        await send({"type": "lifespan.startup.complete"})
        await receive()
        shutdown_entered.set()
        await release_shutdown.wait()
        await send({"type": "lifespan.shutdown.complete"})

    opening = asyncio.create_task(example_runtime._lifespan(held_app))
    await startup_entered.wait()
    assert not opening.done()
    release_startup.set()
    stop = await opening

    async def close() -> None:
        await stop()

    closing = asyncio.create_task(close())
    await shutdown_entered.wait()
    assert not closing.done()
    release_shutdown.set()
    await closing


@pytest.mark.anyio
async def test_lifespan_reports_early_startup_termination_as_incomplete():
    async def early_exit(_scope, receive, _send):
        await receive()

    with pytest.raises(LiveQualificationIncomplete) as caught:
        await example_runtime._lifespan(early_exit)
    assert caught.value.reason == "reference_runtime_startup_not_observed"
