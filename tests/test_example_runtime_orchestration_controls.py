"""Orchestration and refusal controls for the selected example runtime proof (fabricated)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import selected_profiles_example_runtime as runtime
from databricks.sdk import WorkspaceClient
from qualify_live_apps_incomplete import LiveQualificationIncomplete
from tether_databricks import ResourceRef

from tether.client import Actor

_CONSTRUCTOR = runtime._ConstructorInputs("https://workspace.example.test", "profile-1", 1)


def _manifest(tmp_path: Path, text: str | None) -> Path:
    if text is not None:
        (tmp_path / "app.yaml").write_text(text, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("- name: TETHER_PG_CAPABILITY_PROFILE\n  value: plain-1\n", "plain-1"),
        ('- name: TETHER_PG_CAPABILITY_PROFILE\n  value: "quoted.2"\n', "quoted.2"),
    ],
)
def test_bundle_capability_profile_reads_plain_and_quoted(
    tmp_path: Path, text: str, expected: str
) -> None:
    assert runtime._bundle_capability_profile(_manifest(tmp_path, text)) == expected


@pytest.mark.parametrize(
    ("text", "code"),
    [
        (None, "selected_bundle_manifest_unavailable"),
        ("- name: OTHER\n  value: x\n", "selected_bundle_capability_profile_invalid"),
        (
            "- name: TETHER_PG_CAPABILITY_PROFILE\n  other: x\n",
            "selected_bundle_capability_profile_invalid",
        ),
        (
            '- name: TETHER_PG_CAPABILITY_PROFILE\n  value: "unterminated\n',
            "selected_bundle_capability_profile_invalid",
        ),
        (
            "- name: TETHER_PG_CAPABILITY_PROFILE\n  value: has space\n",
            "selected_bundle_capability_profile_invalid",
        ),
        (
            '- name: TETHER_PG_CAPABILITY_PROFILE\n  value: ""\n',
            "selected_bundle_capability_profile_invalid",
        ),
        (
            "- name: TETHER_PG_CAPABILITY_PROFILE\n  value: 12\n  \n"
            "- name: TETHER_PG_CAPABILITY_PROFILE\n  value: a\n",
            "selected_bundle_capability_profile_invalid",
        ),
        (
            '- name: TETHER_PG_CAPABILITY_PROFILE\n  value: "a"\n'
            "- name: TETHER_PG_CAPABILITY_PROFILE\n  value: b\n",
            "selected_bundle_capability_profile_invalid",
        ),
    ],
)
def test_bundle_capability_profile_refuses_malformed_manifests(
    tmp_path: Path, text: str | None, code: str
) -> None:
    with pytest.raises(RuntimeError, match=code):
        runtime._bundle_capability_profile(_manifest(tmp_path, text))


def test_bundle_capability_profile_refuses_unparseable_flow_value(tmp_path: Path) -> None:
    (tmp_path / "app.yaml").write_text(
        "- name: TETHER_PG_CAPABILITY_PROFILE\n  value: {bad\n", encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="selected_bundle_capability_profile_invalid"):
        runtime._bundle_capability_profile(tmp_path)


def test_factories_count_and_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        runtime, "create_api", lambda **kwargs: calls.append(("api", kwargs)) or "api-app"
    )
    monkeypatch.setattr(
        runtime, "create_static_app", lambda **kwargs: calls.append(("ui", kwargs)) or "ui-app"
    )
    factories = runtime._Factories()
    assert (
        factories.api(clients="c", authenticator=cast(Any, "a"), settings=cast(Any, "s"))
        == "api-app"
    )
    assert factories.ui(settings=cast(Any, "u")) == "ui-app"
    assert (factories.api_calls, factories.ui_calls) == (1, 1)
    assert calls[0][1] == {"clients": "c", "authenticator": "a", "settings": "s"}
    assert calls[1][1] == {"settings": "u"}


def test_resource_provider_borrows_source_and_refuses_undeclared_binding() -> None:
    declared = ResourceRef(name="declared", kind="lakebase-postgres")
    other = ResourceRef(name="other", kind="lakebase-postgres")
    settings = SimpleNamespace(resource_bindings=(declared,))

    async def run() -> None:
        provider = await runtime._resource_provider(cast(Any, settings), cast(Any, "source"))
        with pytest.raises(PermissionError, match="resource_not_declared"):
            await provider.acquire(other)
        lease = await provider.acquire(declared)
        assert lease.owned is False
        assert cast(Any, lease).value == "source"
        assert await provider.aclose() == ()
        assert provider.close_calls == 1
        # The inner acquirer itself also refuses an undeclared reference.
        with pytest.raises(PermissionError, match="resource_not_declared"):
            await provider._acquire(other)

    asyncio.run(run())


class _Cursor:
    def __init__(self, row: object = None, rows: tuple[object, ...] = ()) -> None:
        self._row = row
        self._rows = rows

    async def fetchone(self) -> object:
        return self._row

    async def fetchall(self) -> tuple[object, ...]:
        return self._rows


class _Connection:
    """Scripted catalog connection; each fragment maps to a cursor or a callable."""

    def __init__(self, **overrides: _Cursor) -> None:
        self.overrides = overrides

    async def execute(self, query: str, _params: object = ()) -> _Cursor:
        table = {
            "FROM pg_namespace": ("schema", _Cursor(rows=(("s", "o", "acl"),))),
            "FROM pg_roles": ("role", _Cursor(row=(7, False, False, False, False, True))),
            "FROM pg_auth_members": ("members", _Cursor()),
            "has_database_privilege": ("database", _Cursor(row=(True, False))),
            "has_schema_privilege": ("schema_priv", _Cursor(row=(True, False))),
            "FROM pg_class": ("objects", _Cursor()),
            "role_table_grants": ("grants", _Cursor()),
        }
        for fragment, (name, default) in table.items():
            if fragment in query:
                return self.overrides.get(name, default)
        raise AssertionError("unexpected query")


def _source(connection: _Connection) -> Any:
    @asynccontextmanager
    async def source(_actor: object, _session: object):
        yield connection

    return source


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"role": _Cursor()}, "selected_database_role_missing"),
        ({"database": _Cursor()}, "database_privilege_snapshot_missing"),
        ({"schema_priv": _Cursor()}, "schema_privilege_snapshot_missing"),
        (
            {"objects": _Cursor(rows=tuple((str(i),) for i in range(1001)))},
            "schema_object_snapshot_exceeds_bound",
        ),
        (
            {"grants": _Cursor(rows=tuple((str(i),) for i in range(1001)))},
            "schema_grant_snapshot_exceeds_bound",
        ),
    ],
)
def test_snapshot_refuses_missing_or_unbounded_catalog_facts(
    override: dict[str, _Cursor], code: str
) -> None:
    source = _source(_Connection(**override))
    with pytest.raises(RuntimeError, match=code):
        asyncio.run(runtime._snapshot(source, Actor(id="caller"), "s"))


class _FakeApp:
    """ASGI lifespan app scripted by the messages it sends back."""

    def __init__(self, startup: str, shutdown: str | None = "lifespan.shutdown.complete") -> None:
        self.startup = startup
        self.shutdown = shutdown
        self.shutdown_report = None

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        assert scope["type"] == "lifespan"
        assert (await receive())["type"] == "lifespan.startup"
        await send({"type": self.startup})
        if self.startup != "lifespan.startup.complete":
            return
        assert (await receive())["type"] == "lifespan.shutdown"
        if self.shutdown is not None:
            await send({"type": self.shutdown})


def test_lifespan_runs_startup_then_shutdown() -> None:
    async def run() -> None:
        stop = await runtime._lifespan(cast(Any, _FakeApp("lifespan.startup.complete")))
        await stop()

    asyncio.run(run())


def test_lifespan_refuses_unexpected_startup_message() -> None:
    with pytest.raises(RuntimeError, match="reference_runtime_startup_failed"):
        asyncio.run(runtime._lifespan(cast(Any, _FakeApp("lifespan.startup.failed"))))


def test_lifespan_refuses_unexpected_shutdown_message() -> None:
    async def run() -> None:
        stop = await runtime._lifespan(
            cast(Any, _FakeApp("lifespan.startup.complete", "lifespan.shutdown.failed"))
        )
        await stop()

    with pytest.raises(RuntimeError, match="reference_runtime_shutdown_failed"):
        asyncio.run(run())


def test_lifespan_shutdown_without_reply_is_incomplete() -> None:
    async def run() -> None:
        stop = await runtime._lifespan(cast(Any, _FakeApp("lifespan.startup.complete", None)))
        await stop()

    with pytest.raises(
        LiveQualificationIncomplete, match="reference_runtime_shutdown_not_observed"
    ):
        asyncio.run(run())


def test_bindings_settings_requires_durable_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    app_settings = SimpleNamespace(database_profile=None)
    monkeypatch.setattr(runtime, "deployment_settings", lambda _values: (app_settings, "api", "ui"))
    with pytest.raises(RuntimeError, match="durable_database_profile_required"):
        runtime._bindings_settings(
            {}, schema="s", actor=Actor(id="caller"), constructor=_CONSTRUCTOR
        )


def _workspace(
    *,
    user: object = "caller-role",
    authorization: object = "Bearer fabricated",
    host: object = "https://w.example.test",
) -> WorkspaceClient:
    headers = {} if authorization is None else {"authorization": authorization}
    return cast(
        WorkspaceClient,
        SimpleNamespace(
            current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name=user)),
            config=SimpleNamespace(host=host, authenticate=lambda: headers),
        ),
    )


_VALUES = {
    "TETHER_SERVICE_CALLER_DATABASE_ROLE": "caller-role",
    "TETHER_PG_INFRA_ROLE": "infra-role",
    "TETHER_PG_ENDPOINT": "endpoint-1",
    "TETHER_PG_HOST": "database.example.test",
    "TETHER_PG_DATABASE": "db",
}


@pytest.mark.parametrize(
    ("workspace", "values", "code"),
    [
        (_workspace(host=None), _VALUES, "selected_workspace_host_unavailable"),
        (_workspace(host=""), _VALUES, "selected_workspace_host_unavailable"),
        (
            _workspace(),
            {**_VALUES, "DATABRICKS_HOST": "https://other.example.test"},
            "selected_workspace_host_mismatch",
        ),
        (_workspace(user=None), _VALUES, "selected_nonadmin_actor_role_mismatch"),
        (_workspace(authorization=None), _VALUES, "selected_nonadmin_actor_role_mismatch"),
        (_workspace(authorization="Basic x"), _VALUES, "selected_nonadmin_actor_role_mismatch"),
        (_workspace(user="someone"), _VALUES, "selected_nonadmin_actor_role_mismatch"),
        (
            _workspace(),
            {**_VALUES, "TETHER_PG_INFRA_ROLE": "caller-role"},
            "selected_nonadmin_actor_role_mismatch",
        ),
        (
            _workspace(),
            {**_VALUES, "TETHER_PG_INFRA_ROLE": ""},
            "selected_nonadmin_actor_role_mismatch",
        ),
    ],
)
def test_selected_nonadmin_source_refuses_identity_and_host_drift(
    workspace: WorkspaceClient, values: dict[str, str], code: str
) -> None:
    with pytest.raises(RuntimeError, match=code):
        asyncio.run(runtime._selected_nonadmin_source(workspace, values))


def test_selected_nonadmin_source_builds_actor_with_static_credential() -> None:
    actor, source = asyncio.run(
        runtime._selected_nonadmin_source(
            _workspace(),
            {**_VALUES, "TETHER_PG_PORT": "6543", "DATABRICKS_HOST": "https://w.example.test"},
        )
    )
    assert actor.id == "caller-role"
    assert asyncio.run(cast(Any, actor.credential).authorization()) == "Bearer fabricated"
    assert source is not None


@pytest.mark.parametrize(
    ("row", "snapshot_first", "code"),
    [
        ((2,), None, "valid_database_baseline_failed"),
        (
            (1,),
            ((), (0, True, False), (), (), (), (), ()),
            "selected_schema_or_nonadmin_role_invalid",
        ),
        (
            (1,),
            (((("s",)),), (0, False, True), (), (), (), (), ()),
            "selected_schema_or_nonadmin_role_invalid",
        ),
        (
            (1,),
            ((), (0, False, False), (), (), (), (), ()),
            "selected_schema_or_nonadmin_role_invalid",
        ),
    ],
)
def test_check_existing_schema_refusals(
    monkeypatch: pytest.MonkeyPatch,
    row: tuple[int],
    snapshot_first: runtime.Snapshot | None,
    code: str,
) -> None:
    class Conn:
        async def execute(self, _query: str) -> _Cursor:
            return _Cursor(row=row)

    async def snapshot(*_args: object) -> object:
        return snapshot_first

    monkeypatch.setattr(runtime, "_snapshot", snapshot)
    with pytest.raises(RuntimeError, match=code):
        asyncio.run(runtime._check_existing_schema(_source(cast(Any, Conn())), Actor(id="c"), "s"))


def test_check_existing_schema_returns_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    good = ((("s",),), (1, False, False), (), (), (), (), ())

    class Conn:
        async def execute(self, _query: str) -> _Cursor:
            return _Cursor(row=(1,))

    async def snapshot(*_args: object) -> object:
        return good

    monkeypatch.setattr(runtime, "_snapshot", snapshot)
    result = asyncio.run(
        runtime._check_existing_schema(_source(cast(Any, Conn())), Actor(id="c"), "s")
    )
    assert result == good
