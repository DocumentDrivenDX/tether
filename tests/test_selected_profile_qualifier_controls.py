"""Deterministic selected-profile orchestration and packaging controls."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import zipfile
from contextlib import asynccontextmanager
from types import ModuleType, SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
import qualify_selected_profiles as qualifier
import selected_profiles_expiry as expiry
import selected_profiles_identity as identity
import selected_profiles_logs as logs
import selected_profiles_owner as owner
from databricks.sdk import WorkspaceClient
from selected_profile_log_fixtures import privacy_logs
from selected_profiles_privacy_types import PrivacyRejection, PrivacyTurn, PrivacyWindow


def _fake_value(*parts: str) -> str:
    return "-".join(parts)


def _authorization(token: str) -> str:
    return " ".join(("Bearer", token))


def _as_workspace(value: Any) -> WorkspaceClient:
    return cast(WorkspaceClient, value)


def _set_module_attribute(module: ModuleType, name: str, value: Any) -> None:
    setattr(module, name, value)


def _simple_settings(**values: Any) -> Any:
    return SimpleNamespace(**values)


def test_owner_content_turn_closes_session_and_both_clients(monkeypatch) -> None:
    calls: list[str] = []
    import tether.client

    monkeypatch.setattr(tether.client, "Answer", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(owner.uuid, "uuid4", lambda: SimpleNamespace(hex="fixture"))
    final = SimpleNamespace(
        stop_reason="final",
        messages=[SimpleNamespace(role="user", content="fabricated content fixture")],
    )
    client = SimpleNamespace(
        create=AsyncMock(return_value=SimpleNamespace(id="session")),
        send=AsyncMock(
            return_value=SimpleNamespace(
                result=AsyncMock(
                    return_value=SimpleNamespace(stop_reason="confirmation_required", messages=[])
                )
            )
        ),
        pending=AsyncMock(return_value=SimpleNamespace(invocation_id="invocation")),
        confirm=AsyncMock(return_value=SimpleNamespace(result=AsyncMock(return_value=final))),
        close=AsyncMock(side_effect=lambda *_args: calls.append("close-session")),
        aclose=AsyncMock(side_effect=lambda: calls.append("close-client")),
    )
    transport = SimpleNamespace(
        aclose=AsyncMock(side_effect=lambda: calls.append("close-transport"))
    )

    async def remote(*_args: Any, **_kwargs: Any) -> tuple[Any, Any]:
        calls.append("create")
        return client, transport

    monkeypatch.setattr(owner, "_remote", remote)
    workspace = SimpleNamespace(
        config=SimpleNamespace(
            authenticate=lambda: {
                "Authorization": " ".join(("Bearer", _fake_value("fabricated", "token")))
            }
        )
    )

    result = asyncio.run(
        owner.successful_content_turn("https://example.test", "/api", _as_workspace(workspace))
    )
    assert result == PrivacyTurn("fabricated content fixture", "invocation", "session")
    assert calls == ["create", "close-session", "close-client", "close-transport"]


def test_owner_isolation_composes_clients_and_closes_all_resources(monkeypatch) -> None:
    calls: list[str] = []

    class LakebaseSettings:
        def __init__(self, **values: Any) -> None:
            self.values = values

    class LakebaseConnectionSource:
        def __init__(self, config: Any, **kwargs: Any) -> None:
            self.config = config
            self.kwargs = kwargs

    fake_postgres = ModuleType("tether_databricks_example.postgres")
    _set_module_attribute(fake_postgres, "LakebaseSettings", LakebaseSettings)
    _set_module_attribute(fake_postgres, "LakebaseConnectionSource", LakebaseConnectionSource)
    monkeypatch.setitem(sys.modules, "tether_databricks_example.postgres", fake_postgres)
    monkeypatch.setattr(owner, "_remote", _remote_pair(calls))
    monkeypatch.setattr(
        owner,
        "_api_owner_checks",
        _async_call(
            calls,
            "api",
            return_value=(
                SimpleNamespace(id="human-session"),
                SimpleNamespace(id="service-session"),
            ),
        ),
    )

    async def raw(sources: Any, actors: Any, schema: str, ids: Any) -> None:
        calls.append("raw")
        assert schema == "fabricated_schema"
        assert ids == ("human-session", "service-session")
        assert sources[0].config.values["endpoint"] == "fabricated-endpoint"
        assert actors[1].id == "fabricated-service-role"

    monkeypatch.setattr(owner, "_raw_owner_checks", raw)
    human = _workspace("human", "human@example.test")
    service = _workspace("service", "fabricated-service-user")
    settings = SimpleNamespace(
        pg_endpoint="fabricated-endpoint",
        pg_host="db.example.test",
        pg_database="fabricated-db",
        pg_schema="fabricated_schema",
        ca_file="/fabricated/ca.pem",
    )
    asyncio.run(
        owner.owner_isolation(
            "https://example.test", "/api", settings, "fabricated-service-role", human, service
        )
    )
    assert calls == [
        "remote-human",
        "remote-service",
        "api",
        "raw",
        "close-human-session",
        "close-service-session",
        "client-close",
        "client-close",
        "http-close",
        "http-close",
    ]


def _workspace(principal: str, username: str) -> WorkspaceClient:
    return cast(
        WorkspaceClient,
        SimpleNamespace(
            config=SimpleNamespace(
                host="https://workspace.example.test",
                authenticate=lambda: {
                    "Authorization": " ".join(("Bearer", _fake_value(principal, "token")))
                },
            ),
            current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name=username)),
        ),
    )


def _remote_pair(calls: list[str]):
    def make(label: str) -> Any:
        return SimpleNamespace(
            close=AsyncMock(side_effect=lambda *_args: calls.append(f"close-{label}-session")),
            aclose=AsyncMock(side_effect=lambda: calls.append("client-close")),
        )

    async def remote(_origin: str, _prefix: str, _token: str, *, service: bool) -> tuple[Any, Any]:
        label = "service" if service else "human"
        calls.append("remote-" + label)
        client = make(label)
        transport = SimpleNamespace(
            aclose=AsyncMock(side_effect=lambda: calls.append("http-close"))
        )
        return client, transport

    return remote


def test_logs_bounded_reader_and_privacy_window(monkeypatch) -> None:
    async def read() -> None:
        ready = asyncio.Event()

        class Stream:
            def __init__(self, chunks: list[bytes]) -> None:
                self.chunks = iter(chunks)
                self.reads = 0

            async def read(self, _size: int) -> bytes:
                self.reads += 1
                return next(self.chunks, b"")

        assert (
            await logs._read_bounded(cast(asyncio.StreamReader, Stream([b"one", b"two"])), ready)
            == b"onetwo"
        )
        assert ready.is_set()
        over_limit = Stream([b"12345", b"drained"])
        with pytest.raises(RuntimeError, match="platform_log_capture_limit_exceeded"):
            await logs._read_bounded(
                cast(asyncio.StreamReader, over_limit), asyncio.Event(), limit=4
            )
        assert over_limit.reads == 3

        sources: list[str | None] = []

        class Follower:
            def __init__(self, source: str | None) -> None:
                self.process = SimpleNamespace(returncode=None)
                self.ready = asyncio.Event()
                self.ready.set()
                self.source = source

        async def start(_profile: str, _app: str, source: str | None) -> Any:
            sources.append(source)
            return Follower(source)

        async def stop(followers: list[Any]) -> list[tuple[bytes, bytes]]:
            return [
                (b"TETHER_QUALIFICATION_WORKER_STARTED invocation", b"")
                if f.source == "APP"
                else (b"system-log", b"")
                for f in followers
            ]

        async def wait_for_witnesses(
            followers: list[Any],
            success: PrivacyTurn,
            rejected: PrivacyRejection,
            api_prefix: str,
        ) -> None:
            assert [follower.source for follower in followers] == ["APP", None]
            assert (success.invocation_id, success.session_id) == ("invocation", "session")
            assert rejected.session_id == "rejected"
            assert api_prefix == "/api"

        monkeypatch.setattr(logs, "_start_follower", start)
        monkeypatch.setattr(logs, "_stop_followers", stop)
        monkeypatch.setattr(logs, "_wait_for_privacy_witnesses", wait_for_witnesses)

        async def success() -> PrivacyTurn:
            assert sources == ["APP", None]
            return PrivacyTurn("secret content", "invocation", "session")

        async def rejection() -> PrivacyRejection:
            assert sources == ["APP", None]
            return PrivacyRejection("rejected content", "secret credential", "rejected")

        result = await logs.capture_privacy_window(
            "profile",
            "app",
            success,
            rejection,
            api_prefix="/api",
        )
        assert result == PrivacyWindow(
            app_logs="TETHER_QUALIFICATION_WORKER_STARTED invocation",
            platform_logs="system-log",
            diagnostics=("", ""),
            success=PrivacyTurn("secret content", "invocation", "session"),
            rejected=PrivacyRejection("rejected content", "secret credential", "rejected"),
        )
        assert sources == ["APP", None]

    asyncio.run(read())


def test_qualifier_small_refusal_and_privacy_predicates() -> None:
    from tether.client import IntegrityError

    assert (
        qualifier._missing_schema_refusal_code(IntegrityError("database_schema_mismatch"))
        == "database_schema_mismatch"
    )

    class ClassifiedError(RuntimeError):
        sqlstate = "3F000"

    assert qualifier._missing_schema_refusal_code(ClassifiedError("fabricated")) == "3F000"
    assert qualifier._missing_schema_refusal_code(RuntimeError("unrelated")) is None
    with pytest.raises(RuntimeError, match="revocation_evidence_requires_genuine_expiry_case"):
        qualifier._validate_case_dependencies(["US-068-AC3"])
    qualifier._validate_case_dependencies(["US-068-AC3", "US-068-AC6"])

    app, platform = privacy_logs("invocation")
    qualifier.log_privacy(
        app,
        platform,
        ("fabricated content", "rejected content"),
        _fake_value("fabricated", "credential"),
        "invocation",
        success_session_id="privacy-session",
        rejected_session_id="privacy-refused",
        api_prefix="/api",
    )
    with pytest.raises(RuntimeError, match="privacy_app_window_not_anchored"):
        qualifier.log_privacy(
            privacy_logs("old-invocation")[0],
            platform,
            ("content",),
            "credential",
            "invocation",
            success_session_id="privacy-session",
            rejected_session_id="privacy-refused",
            api_prefix="/api",
        )
    with pytest.raises(RuntimeError, match="privacy_marker_found_in_platform_logs"):
        qualifier.log_privacy(
            "TETHER_QUALIFICATION_WORKER_STARTED invocation fabricated content",
            "system output",
            ("fabricated content",),
            "credential",
            "invocation",
            success_session_id="privacy-session",
            rejected_session_id="privacy-refused",
            api_prefix="/api",
        )


def test_qualifier_run_cases_dispatches_each_selected_profile_case(monkeypatch, tmp_path) -> None:
    import selected_profiles_bundle as bundle_module
    import selected_profiles_example_runtime as example_runtime

    calls: list[str] = []
    monkeypatch.setattr(owner, "owner_isolation", _async_call(calls, "owner"))
    monkeypatch.setattr(
        identity,
        "revocation_and_refusal",
        _async_call(calls, "identity", return_value={"revocation_token_unexpired_at_denial": True}),
    )
    monkeypatch.setattr(identity, "principal_separation", _async_call(calls, "separation"))
    monkeypatch.setattr(
        expiry, "credential_rotation", _async_call(calls, "expiry", return_value={"ok": 1})
    )
    monkeypatch.setattr(
        example_runtime,
        "selected_example_schema_refusal",
        _async_call(calls, "schema"),
    )
    monkeypatch.setattr(qualifier, "locked_bundle", lambda _path: calls.append("locked"))
    monkeypatch.setattr(
        bundle_module,
        "deployed_bundle_matches_lock",
        _async_call(calls, "deployed"),
    )
    monkeypatch.setattr(qualifier, "_authorization", lambda _workspace: "safe bearer")
    monkeypatch.setattr(
        qualifier,
        "exercise_privacy_route",
        _async_call(
            calls,
            "rejected",
            return_value=PrivacyRejection("content", "credential", "privacy-refused"),
        ),
    )
    monkeypatch.setattr(
        owner,
        "successful_content_turn",
        _async_call(
            calls,
            "success",
            return_value=PrivacyTurn("other content", "invocation", "privacy-session"),
        ),
    )

    async def capture(*args: Any, **kwargs: Any) -> PrivacyWindow:
        assert kwargs == {"api_prefix": "/api"}
        calls.append("capture")
        successful = await args[2]()
        rejected = await args[3]()
        app, platform = privacy_logs("invocation")
        return PrivacyWindow(app, platform, ("", ""), successful, rejected)

    monkeypatch.setattr(logs, "capture_privacy_window", capture)
    cases = list(qualifier.PROFILE_CASE_IDS)
    args = SimpleNamespace(
        case=cases,
        profile="human-profile",
        service_profile="service-profile",
        service_config_file="config",
        service_secret_reference=_fake_value("fabricated", "keychain", "reference"),
        identity_reference=tmp_path / "identity.json",
        deployment_reference_file=tmp_path / "deployment.json",
        bundle=tmp_path,
    )
    observed, observations = asyncio.run(
        qualifier._run_cases(
            cast(argparse.Namespace, args),
            origin="https://example.test",
            prefix="/api",
            settings=SimpleNamespace(pg_endpoint="endpoint"),
            values={
                "TETHER_SERVICE_CALLER_DATABASE_ROLE": "service",
                "TETHER_PG_INFRA_ROLE": "infra",
            },
            app_name="app",
            app_principal_id="infra",
            app=SimpleNamespace(),
            human_workspace=_as_workspace(SimpleNamespace()),
            service_workspace=_as_workspace(SimpleNamespace()),
            credential_factory=AsyncMock(),
        )
    )
    assert [case for case, _elapsed in observed] == cases
    assert observations["US-068-AC6"] == {"ok": 1}
    assert {
        "owner",
        "identity",
        "separation",
        "expiry",
        "schema",
        "locked",
        "deployed",
        "capture",
        "success",
        "rejected",
    } <= set(calls)


def _async_call(calls: list[str], label: str, *, return_value: Any = None):
    async def call(*_args: Any, **_kwargs: Any) -> Any:
        calls.append(label)
        return return_value

    return call


def test_qualifier_execute_writes_receipt_only_after_case_success(monkeypatch, tmp_path) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"url": "https://example.test", "api_prefix": "/api"}))
    app_reference = tmp_path / "app.json"
    app_reference.write_text(json.dumps({"name": "fabricated-app"}))
    output = tmp_path / "receipt.json"
    args = SimpleNamespace(
        output=output,
        case=["US-068-AC2"],
        settings_file=settings_file,
        profile="fabricated-profile",
        service_profile="fabricated-service",
        service_config_file="fabricated-config",
        service_secret_reference=_fake_value("fabricated", "reference"),
        app_reference_file=app_reference,
        identity_reference=tmp_path / "identity.json",
        deployment_reference_file=tmp_path / "deployment.json",
        bundle=tmp_path,
        browser_receipt=tmp_path / "browser.json",
    )
    monkeypatch.setattr(qualifier, "inventory", lambda _root: {"source.py": "digest"})
    monkeypatch.setattr(
        qualifier,
        "profile_from_environment",
        lambda _values: SimpleNamespace(url="https://example.test", api_prefix="/api"),
    )
    monkeypatch.setattr(qualifier, "safe_origin", lambda url: url)
    monkeypatch.setattr(qualifier, "browser_versions", lambda *_args, **_kwargs: {"Python": "3.11"})
    import selected_profiles_identity_credentials as credentials

    fresh = SimpleNamespace(host="https://workspace.example.test")

    async def acquire(*received: Any) -> Any:
        assert received == (
            "fabricated-service",
            "fabricated-config",
            _fake_value("fabricated", "reference"),
            "https://workspace.example.test",
        )
        return fresh

    def static_workspace(value: Any) -> Any:
        assert value is fresh
        return SimpleNamespace()

    monkeypatch.setattr(credentials, "refresh_workspace_credential", acquire)
    monkeypatch.setattr(credentials, "workspace_from_credential", static_workspace)
    monkeypatch.setattr(
        qualifier,
        "WorkspaceClient",
        lambda **_kwargs: SimpleNamespace(
            config=SimpleNamespace(host="https://workspace.example.test"),
            apps=SimpleNamespace(
                get=lambda _name: SimpleNamespace(service_principal_client_id="fabricated-app-id")
            ),
        ),
    )

    async def run_cases(
        *_args: Any, **_kwargs: Any
    ) -> tuple[list[tuple[str, float]], dict[str, object]]:
        return [("US-068-AC2", 0.01)], {}

    monkeypatch.setattr(qualifier, "_run_cases", run_cases)
    receipts: list[Any] = []

    def write_receipt(*write_args: Any, **kwargs: Any) -> None:
        receipts.append((write_args, kwargs))
        output.write_text("written")

    monkeypatch.setattr(qualifier, "write_selected_receipt", write_receipt)
    qualifier._execute(cast(argparse.Namespace, args))
    assert len(receipts) == 1
    assert receipts[0][1]["cases"][0][0] == "US-068-AC2"
    with pytest.raises(RuntimeError, match="selected_profile_receipt_already_exists"):
        qualifier._execute(cast(argparse.Namespace, args))


def test_locked_bundle_checks_wheels_assets_lock_and_node_absence(tmp_path) -> None:
    bundle = tmp_path / "bundle"
    _make_locked_bundle(bundle)
    qualifier.locked_bundle(bundle)

    (bundle / "node").write_text("fabricated executable")
    with pytest.raises(RuntimeError, match="deployment_bundle_node_runtime_present"):
        qualifier.locked_bundle(bundle)
    (bundle / "node").unlink()

    (bundle / "requirements.txt").write_text("unlocked")
    with pytest.raises(RuntimeError, match="deployment_bundle_has_unlocked_runtime"):
        qualifier.locked_bundle(bundle)


def test_missing_schema_refusal_uses_valid_connection_and_no_mutation(monkeypatch) -> None:
    import tether.client.postgres as client_postgres
    from tether.client import IntegrityError

    class Cursor:
        def __init__(self, row: Any) -> None:
            self.row = row

        async def fetchone(self) -> Any:
            return self.row

    class Connection:
        def __init__(self) -> None:
            self.catalog_reads = 0

        async def execute(self, query: str, _params: Any = None) -> Cursor:
            if query == "SELECT 1":
                return Cursor((1,))
            self.catalog_reads += 1
            return Cursor((0,))

    connection = Connection()

    class Source:
        def __init__(self, config: Any, **kwargs: Any) -> None:
            self.config = config
            self.kwargs = kwargs

        @asynccontextmanager
        async def __call__(self, _actor: Any, _session: Any):
            yield connection

    parent = ModuleType("tether_databricks_example")
    _set_module_attribute(parent, "__path__", [])
    postgres_module = ModuleType("tether_databricks_example.postgres")
    _set_module_attribute(postgres_module, "LakebaseConnectionSource", Source)
    _set_module_attribute(postgres_module, "LakebaseSettings", _simple_settings)
    _set_module_attribute(parent, "postgres", postgres_module)
    monkeypatch.setitem(sys.modules, "tether_databricks_example", parent)
    monkeypatch.setitem(sys.modules, "tether_databricks_example.postgres", postgres_module)

    async def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise IntegrityError("database_schema_mismatch")

    monkeypatch.setattr(client_postgres.PostgresResources, "open", refuse)
    workspace = _workspace("fabricated-user-id", "fabricated-db-user")
    settings = SimpleNamespace(
        pg_endpoint="endpoint", pg_host="host", pg_database="db", ca_file="ca"
    )
    asyncio.run(
        qualifier.missing_schema_refusal(
            workspace=_as_workspace(workspace),
            settings=settings,
            schema="fabricated_missing_schema",
            infra_role="fabricated-infra-role",
        )
    )
    assert connection.catalog_reads == 2


def test_missing_schema_refusal_rejects_unclassified_or_mutating_failure(monkeypatch) -> None:
    import tether.client.postgres as client_postgres

    async def unrelated(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("fabricated transport failure")

    monkeypatch.setattr(client_postgres.PostgresResources, "open", unrelated)
    # Import isolation is kept explicit so the test cannot call a real workspace or database.
    parent = ModuleType("tether_databricks_example")
    _set_module_attribute(parent, "__path__", [])
    postgres_module = ModuleType("tether_databricks_example.postgres")

    class Cursor:
        def __init__(self, row: Any) -> None:
            self.row = row

        async def fetchone(self) -> Any:
            return self.row

    class Connection:
        async def execute(self, query: str, _params: Any = None) -> Cursor:
            return Cursor((1,) if query == "SELECT 1" else (0,))

    class Source:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        @asynccontextmanager
        async def __call__(self, *_args: Any, **_kwargs: Any):
            yield Connection()

    _set_module_attribute(postgres_module, "LakebaseConnectionSource", Source)
    _set_module_attribute(postgres_module, "LakebaseSettings", _simple_settings)
    _set_module_attribute(parent, "postgres", postgres_module)
    monkeypatch.setitem(sys.modules, "tether_databricks_example", parent)
    monkeypatch.setitem(sys.modules, "tether_databricks_example.postgres", postgres_module)

    with pytest.raises(RuntimeError, match="missing_schema_refusal_unclassified"):
        asyncio.run(
            qualifier.missing_schema_refusal(
                workspace=_workspace("user", "db-user"),
                settings=SimpleNamespace(
                    pg_endpoint="ep", pg_host="host", pg_database="db", ca_file="ca"
                ),
                schema="fabricated",
                infra_role="infra",
            )
        )


def test_qualifier_profile_helpers_keep_secrets_in_explicit_sources(monkeypatch, tmp_path) -> None:
    reference = tmp_path / "keychain.json"
    reference.write_text(
        json.dumps(
            {
                "service": _fake_value("fabricated", "service"),
                "account": _fake_value("fabricated", "account"),
            }
        )
    )
    monkeypatch.setattr(
        qualifier,
        "_security_secret",
        lambda service, account: _fake_value(service, account, "secret"),
    )
    constructed: list[dict[str, Any]] = []

    def workspace(**kwargs: Any) -> Any:
        constructed.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(qualifier, "WorkspaceClient", workspace)
    qualifier._service_workspace("/fabricated/config", str(reference), "fabricated-profile")
    assert constructed == [
        {
            "profile": "fabricated-profile",
            "config_file": "/fabricated/config",
            "client_secret": _fake_value("fabricated-service", "fabricated-account", "secret"),
            "auth_type": "oauth-m2m",
        }
    ]
    assert qualifier._authorization(
        _as_workspace(
            SimpleNamespace(
                config=SimpleNamespace(
                    authenticate=lambda: {
                        "authorization": _authorization(_fake_value("fabricated"))
                    }
                )
            )
        )
    ) == _authorization(_fake_value("fabricated"))
    with pytest.raises(RuntimeError, match="selected_profile_credential_missing"):
        qualifier._authorization(
            _as_workspace(SimpleNamespace(config=SimpleNamespace(authenticate=lambda: {})))
        )


def test_privacy_route_classifies_refusal(monkeypatch) -> None:
    class Response:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

    class HttpClient:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["trust_env"] is False
            self.response = Response(403)

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def post(self, _route: str, *, json: Any, headers: Any) -> Response:
            assert json["text"].startswith("fabricated content ")
            assert _route.startswith("/api/sessions/")
            assert _route.endswith("/turns")
            assert headers["X-Tether-Service-Caller-Authorization"].startswith("Bearer ")
            return self.response

    monkeypatch.setattr("httpx.AsyncClient", HttpClient)
    result = asyncio.run(
        qualifier.exercise_privacy_route(
            "https://example.test", "/api", _authorization(_fake_value("fabricated"))
        )
    )
    assert result.content_marker.startswith("fabricated content ")
    assert result.credential_marker.startswith("fabricated-credential-")
    assert result.session_id


def test_parse_args_and_main_report_safe_failure(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "qualify_selected_profiles.py",
            "--profile",
            "fabricated-human",
            "--service-profile",
            "fabricated-service",
            "--service-config-file",
            "config",
            "--service-secret-reference",
            "ref",
            "--settings-file",
            "settings.json",
            "--app-reference-file",
            "app.json",
            "--identity-reference",
            "identity.json",
            "--deployment-reference-file",
            "deployment.json",
            "--bundle",
            "bundle",
            "--output",
            "receipt.json",
            "--browser-receipt",
            "browser.json",
            "--case",
            "US-068-AC2",
        ],
    )
    args = qualifier._parse_args()
    assert args.case == ["US-068-AC2"]
    monkeypatch.setattr(qualifier, "exec_with_profile_environment", lambda *_args: None)
    monkeypatch.setattr(qualifier, "_parse_args", lambda: SimpleNamespace())
    monkeypatch.setattr(
        qualifier, "_execute", lambda _args: (_ for _ in ()).throw(ValueError("secret-like"))
    )
    assert qualifier.main() == 1
    assert capsys.readouterr().err.strip() == "selected_profile_check_failed=ValueError"


def _make_locked_bundle(bundle: Any) -> None:
    bundle.mkdir()
    for name in ("pyproject.toml", "databricks.yml", "qualification-ca.pem"):
        (bundle / name).write_text("fabricated")
    (bundle / "app.yaml").write_text('command: ["uv", "run", "--locked"]')
    names = (
        "novirhiza-tether",
        "novirhiza-tether-web-api",
        "novirhiza-tether-web-ui",
        "novirhiza-tether-databricks",
    )
    package_lines: list[str] = []
    wheelhouse = bundle / "wheelhouse"
    wheelhouse.mkdir()
    for name in names:
        package_lines.extend(("[[package]]", f'name = "{name}"', 'version = "0.2.0.dev0"', ""))
        safe_name = name.replace("-", "_")
        path = wheelhouse / f"{safe_name}-0.2.0.dev0-py3-none-any.whl"
        metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: 0.2.0.dev0\n\n"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(f"{safe_name}-0.2.0.dev0.dist-info/METADATA", metadata)
            if name.endswith("web-ui"):
                archive.writestr("tether_web_ui/assets/app.js", "fabricated js")
                archive.writestr("tether_web_ui/assets/app.css", "fabricated css")
    (bundle / "uv.lock").write_text("\n".join(package_lines))
