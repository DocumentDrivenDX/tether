"""Carrier behavior tests use fabricated actors, bindings and ASGI apps."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, cast

import pytest
from fastapi import Request
from tether_databricks import (
    CarrierASGI,
    DatabricksAppSettings,
    DatabricksIdentityResolver,
    DatabricksResources,
    ResourceLease,
    ResourceLifecycle,
    ResourceRef,
    ServiceCallerIdentity,
    ShutdownReport,
    create_carrier_app,
)
from tether_web_api import ApiFactory, ApiSettings
from tether_web_ui import UiSettings

from tether.client import Actor, ClientProvider


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _bearer(value: str) -> str:
    return " ".join(("Bearer", value))


def request(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request({"type": "http", "headers": headers, "method": "GET", "path": "/"})


class LocalAuthenticator:
    def __init__(self, actor: Actor) -> None:
        self.actor = actor
        self.calls = 0

    async def actor_for(self, request: Request) -> Actor:
        _ = request
        self.calls += 1
        return self.actor


class Lease:
    def __init__(self, value: object, *, owned: bool) -> None:
        self.value, self.owned = value, owned
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


@pytest.mark.anyio
async def test_apps_identity_is_current_request_scoped_and_redacted() -> None:
    """@covers US-068-AC4"""
    resolver = DatabricksIdentityResolver(trusted_ingress="databricks-apps")
    actor = await resolver.actor_for(
        request(
            [
                (b"X-Forwarded-User", b"fabricated-user"),
                (b"X-Forwarded-Email", b"fabricated-user@example.invalid"),
                (b"X-Forwarded-Access-Token", b"fabricated-token"),
            ]
        )
    )
    assert actor.id == "fabricated-user@example.invalid"
    assert actor.principal == "fabricated-user"
    assert "fabricated-token" not in repr(actor)
    assert "credential" not in actor.model_dump(mode="json")
    assert actor.credential is not None
    assert await actor.credential.authorization() == _bearer("fabricated-token")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"x-forwarded-user", b"fabricated-user")],
        [
            (b"x-forwarded-user", b"fabricated-user"),
            (b"x-forwarded-access-token", b"fabricated-token"),
        ],
        [
            (b"x-forwarded-user", b"fabricated-user"),
            (b"x-forwarded-email", b"fabricated-user@example.invalid"),
            (b"x-forwarded-user", b"other-user"),
            (b"x-forwarded-access-token", b"fabricated-token"),
        ],
        [
            (b"x-forwarded-user", b"fabricated-user"),
            (b"x-forwarded-email", b"fabricated-user@example.invalid"),
            (b"x-forwarded-access-token", b"fabricated-token,other"),
        ],
        [
            (b"x-forwarded-user", b"fabricated-user"),
            (b"x-forwarded-email", b"not-an-email"),
            (b"x-forwarded-access-token", b"fabricated-token"),
        ],
    ],
)
async def test_apps_identity_fails_closed_on_missing_or_ambiguous_headers(
    headers: list[tuple[bytes, bytes]],
) -> None:
    """@covers US-068-AC3"""
    from tether_web_api import ApiFailure

    resolver = DatabricksIdentityResolver(trusted_ingress="databricks-apps")
    with pytest.raises(ApiFailure) as error:
        await resolver.actor_for(request(headers))
    assert error.value.status == 401
    assert "fabricated-token" not in str(error.value)


@pytest.mark.anyio
async def test_explicit_service_caller_gets_distinct_actor_and_request_credential() -> None:
    """@covers US-068-AC3 @covers US-068-AC4"""
    seen: list[str] = []

    async def authenticate(token: str) -> ServiceCallerIdentity:
        seen.append(token)
        return ServiceCallerIdentity(
            actor_id="fabricated-dedicated-db-role", principal="fabricated-sp-id"
        )

    prepared: list[Actor] = []

    async def prepare(actor: Actor) -> None:
        prepared.append(actor)

    resolver = DatabricksIdentityResolver(
        trusted_ingress="databricks-apps",
        service_caller_authenticator=authenticate,
        prepare_actor=prepare,
    )
    actor = await resolver.actor_for(
        request([(b"authorization", _bearer("fabricated-current-token").encode())])
    )
    assert actor.id == "fabricated-dedicated-db-role"
    assert actor.principal == "fabricated-sp-id"
    assert actor.kind == "service"
    assert actor.credential is not None
    assert await actor.credential.authorization() == _bearer("fabricated-current-token")
    assert seen == ["fabricated-current-token"]
    assert prepared == [actor]
    assert "fabricated-current-token" not in repr(actor)


@pytest.mark.anyio
async def test_projected_credential_requires_verified_matching_identity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """@covers US-068-AC3 @covers US-068-AC4"""
    from tether_web_api import ApiFailure

    expected_bearer = "qualification-bearer"

    async def authenticate(bearer: str) -> ServiceCallerIdentity | None:
        if bearer != expected_bearer:
            return None
        return ServiceCallerIdentity(
            actor_id="fabricated-dedicated-db-role",
            principal="fabricated-sp-id",
            forwarded_email="fabricated-application-id",
        )

    resolver = DatabricksIdentityResolver(
        trusted_ingress="databricks-apps",
        service_caller_authenticator=authenticate,
        service_caller_credential_header="x-tether-service-caller-authorization",
    )
    actor = await resolver.actor_for(
        request(
            [
                (
                    b"x-tether-service-caller-authorization",
                    _bearer("qualification-bearer").encode(),
                ),
                (b"x-forwarded-user", b"fabricated-idp-subject-one"),
                (b"x-forwarded-email", b"fabricated-application-id"),
                (b"x-forwarded-access-token", b"different-forwarded-token"),
            ]
        )
    )
    assert actor.id == "fabricated-dedicated-db-role"
    assert actor.principal == "fabricated-sp-id"
    assert actor.credential is not None
    assert await actor.credential.authorization() == _bearer("qualification-bearer")
    changed_user = await resolver.actor_for(
        request(
            [
                (
                    b"x-tether-service-caller-authorization",
                    _bearer("qualification-bearer").encode(),
                ),
                (b"x-forwarded-user", b"fabricated-idp-subject-two"),
                (b"x-forwarded-email", b"fabricated-application-id"),
            ]
        )
    )
    assert changed_user.id == actor.id
    assert changed_user.principal == actor.principal
    with pytest.raises(ApiFailure) as mismatch:
        await resolver.actor_for(
            request(
                [
                    (
                        b"x-tether-service-caller-authorization",
                        _bearer("qualification-bearer").encode(),
                    ),
                    (b"x-forwarded-user", b"fabricated-idp-subject-one"),
                    (b"x-forwarded-email", b"fabricated-other-username"),
                ]
            )
        )
    assert mismatch.value.status == 401
    assert "forwarded_user_present=True" in caplog.text
    assert "email_matches_verified_username=False" in caplog.text
    assert "fabricated-other-username" not in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    "forwarded",
    [[b"first-forwarded-token", b"second-forwarded-token"]],
)
async def test_projected_forwarded_token_diagnostic_is_fixed_and_value_free(
    caplog: pytest.LogCaptureFixture,
    forwarded: list[bytes],
) -> None:
    """@covers US-068-AC3 @covers US-068-AC10"""
    from tether_web_api import ApiFailure

    async def authenticate(_bearer: str) -> ServiceCallerIdentity:
        return ServiceCallerIdentity(
            actor_id="fabricated-dedicated-db-role",
            principal="fabricated-sp-id",
            forwarded_email="fabricated-application-id",
        )

    resolver = DatabricksIdentityResolver(
        trusted_ingress="databricks-apps",
        service_caller_authenticator=authenticate,
        service_caller_credential_header="x-tether-service-caller-authorization",
    )
    headers = [
        (
            b"x-tether-service-caller-authorization",
            _bearer("qualification-bearer").encode(),
        ),
        (b"x-forwarded-user", b"fabricated-sp-id"),
        (b"x-forwarded-email", b"fabricated-application-id"),
        *((b"x-forwarded-access-token", value) for value in forwarded),
    ]
    with pytest.raises(ApiFailure) as error:
        await resolver.actor_for(request(headers))
    assert error.value.status == 401
    assert "reason=forwarded_token_ambiguous" in caplog.text
    assert "qualification-bearer" not in caplog.text
    assert "first-forwarded-token" not in caplog.text
    assert "second-forwarded-token" not in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("header", "value", "reason"),
    [
        (b"x-forwarded-user", b"invalid\nidentity", "forwarded_user_invalid"),
        (b"x-forwarded-email", b"invalid\nidentity", "forwarded_email_invalid"),
    ],
)
async def test_projected_identity_shape_diagnostic_is_field_specific_and_value_free(
    caplog: pytest.LogCaptureFixture,
    header: bytes,
    value: bytes,
    reason: str,
) -> None:
    """@covers US-068-AC3 @covers US-068-AC10"""
    from tether_web_api import ApiFailure

    async def authenticate(_bearer: str) -> ServiceCallerIdentity:
        return ServiceCallerIdentity(
            actor_id="fabricated-dedicated-db-role",
            principal="fabricated-sp-id",
            forwarded_email="fabricated-application-id",
        )

    resolver = DatabricksIdentityResolver(
        trusted_ingress="databricks-apps",
        service_caller_authenticator=authenticate,
        service_caller_credential_header="x-tether-service-caller-authorization",
    )
    headers = [
        (
            b"x-tether-service-caller-authorization",
            _bearer("qualification-bearer").encode(),
        ),
        (b"x-forwarded-user", b"fabricated-sp-id"),
        (b"x-forwarded-email", b"fabricated-application-id"),
    ]
    headers = [(key, value if key == header else old) for key, old in headers]
    with pytest.raises(ApiFailure) as error:
        await resolver.actor_for(request(headers))
    assert error.value.status == 401
    assert f"reason={reason}" in caplog.text
    assert "invalid\nidentity" not in caplog.text


@pytest.mark.anyio
async def test_projected_service_caller_requires_both_matching_forwarded_identities() -> None:
    """@covers US-068-AC3"""
    from tether_web_api import ApiFailure

    calls = 0

    async def authenticate(_bearer: str) -> ServiceCallerIdentity:
        nonlocal calls
        calls += 1
        return ServiceCallerIdentity(
            actor_id="fabricated-dedicated-db-role",
            principal="fabricated-sp-id",
            forwarded_email="fabricated-application-id",
        )

    resolver = DatabricksIdentityResolver(
        trusted_ingress="databricks-apps",
        service_caller_authenticator=authenticate,
        service_caller_credential_header="x-tether-service-caller-authorization",
    )
    with pytest.raises(ApiFailure) as missing:
        await resolver.actor_for(
            request(
                [
                    (
                        b"x-tether-service-caller-authorization",
                        _bearer("qualification-bearer").encode(),
                    ),
                    (b"x-forwarded-user", b"fabricated-sp-id"),
                ]
            )
        )
    assert missing.value.status == 401
    assert calls == 0


@pytest.mark.anyio
async def test_projected_service_caller_rejects_empty_verified_owner() -> None:
    """@covers US-068-AC3"""
    from tether_web_api import ApiFailure

    async def authenticate(_bearer: str) -> ServiceCallerIdentity:
        return ServiceCallerIdentity(
            actor_id="",
            principal="fabricated-sp-id",
            forwarded_email="fabricated-application-id",
        )

    resolver = DatabricksIdentityResolver(
        trusted_ingress="databricks-apps",
        service_caller_authenticator=authenticate,
        service_caller_credential_header="x-tether-service-caller-authorization",
    )
    with pytest.raises(ApiFailure) as error:
        await resolver.actor_for(
            request(
                [
                    (
                        b"x-tether-service-caller-authorization",
                        _bearer("qualification-bearer").encode(),
                    ),
                    (b"x-forwarded-user", b"fabricated-sp-id"),
                    (b"x-forwarded-email", b"fabricated-application-id"),
                ]
            )
        )
    assert error.value.status == 401


@pytest.mark.anyio
async def test_projected_service_caller_rejects_unverified_bearer() -> None:
    """@covers US-068-AC3"""
    from tether_web_api import ApiFailure

    prepared: list[Actor] = []

    async def authenticate(_bearer: str) -> ServiceCallerIdentity | None:
        return None

    async def prepare(actor: Actor) -> None:
        prepared.append(actor)

    resolver = DatabricksIdentityResolver(
        trusted_ingress="databricks-apps",
        service_caller_authenticator=authenticate,
        service_caller_credential_header="x-tether-service-caller-authorization",
        prepare_actor=prepare,
    )
    with pytest.raises(ApiFailure) as error:
        await resolver.actor_for(
            request(
                [
                    (
                        b"x-tether-service-caller-authorization",
                        _bearer("qualification-bearer").encode(),
                    ),
                    (b"x-forwarded-user", b"fabricated-sp-id"),
                    (b"x-forwarded-email", b"fabricated-application-id"),
                ]
            )
        )
    assert error.value.status == 401
    assert prepared == []


@pytest.mark.anyio
async def test_projected_service_caller_header_requires_explicit_configuration() -> None:
    """@covers US-068-AC3"""
    from tether_web_api import ApiFailure

    resolver = DatabricksIdentityResolver(trusted_ingress="databricks-apps")
    with pytest.raises(ApiFailure) as error:
        await resolver.actor_for(
            request(
                [
                    (
                        b"x-tether-service-caller-authorization",
                        _bearer("fabricated-current-token").encode(),
                    )
                ]
            )
        )
    assert error.value.status == 401


@pytest.mark.anyio
async def test_configured_projection_never_falls_back_to_authorization() -> None:
    """@covers US-068-AC3"""
    from tether_web_api import ApiFailure

    calls = 0

    async def authenticate(_bearer: str) -> ServiceCallerIdentity:
        nonlocal calls
        calls += 1
        return ServiceCallerIdentity(actor_id="dedicated-role", principal="caller")

    resolver = DatabricksIdentityResolver(
        trusted_ingress="databricks-apps",
        service_caller_authenticator=authenticate,
        service_caller_credential_header="x-tether-service-caller-authorization",
    )
    with pytest.raises(ApiFailure) as error:
        await resolver.actor_for(request([(b"authorization", _bearer("ignored").encode())]))
    assert error.value.status == 401
    assert calls == 0


@pytest.mark.anyio
async def test_service_caller_does_not_downgrade_partial_forwarded_identity() -> None:
    """@covers US-068-AC3"""
    from tether_web_api import ApiFailure

    calls = 0

    async def authenticate(_token: str) -> ServiceCallerIdentity:
        nonlocal calls
        calls += 1
        return ServiceCallerIdentity(actor_id="role", principal="service")

    resolver = DatabricksIdentityResolver(
        trusted_ingress="databricks-apps",
        service_caller_authenticator=authenticate,
    )
    with pytest.raises(ApiFailure) as error:
        await resolver.actor_for(
            request(
                [
                    (b"authorization", _bearer("token").encode()),
                    (b"x-forwarded-email", b"partial@example.invalid"),
                ]
            )
        )
    assert error.value.status == 401
    assert calls == 0


@pytest.mark.anyio
async def test_service_caller_rejects_duplicate_bearers_and_provider_failures_safely() -> None:
    """@covers US-068-AC3 @covers US-068-AC10"""
    from tether_web_api import ApiFailure

    calls = 0

    async def unavailable(_token: str) -> ServiceCallerIdentity | None:
        nonlocal calls
        calls += 1
        raise RuntimeError("secret-bearing provider exception")

    resolver = DatabricksIdentityResolver(
        trusted_ingress="databricks-apps",
        service_caller_authenticator=unavailable,
    )
    with pytest.raises(ApiFailure) as duplicate:
        await resolver.actor_for(
            request(
                [
                    (b"authorization", _bearer("one").encode()),
                    (b"authorization", _bearer("two").encode()),
                ]
            )
        )
    assert duplicate.value.status == 401
    assert calls == 0
    with pytest.raises(ApiFailure) as failed:
        await resolver.actor_for(request([(b"authorization", _bearer("token").encode())]))
    assert failed.value.status == 503
    assert "secret-bearing" not in str(failed.value)
    assert calls == 1


@pytest.mark.anyio
async def test_local_identity_ignores_forwarded_headers_and_runs_actor_preparation() -> None:
    actor = Actor(id="local-user")
    local = LocalAuthenticator(actor)
    prepared: list[str] = []

    async def prepare(actor: Actor) -> None:
        prepared.append(actor.id)

    resolver = DatabricksIdentityResolver(
        trusted_ingress="local", local_authenticator=local, prepare_actor=prepare
    )
    result = await resolver.actor_for(
        request(
            [
                (b"x-forwarded-user", b"spoofed"),
                (b"x-forwarded-email", b"spoofed@example.invalid"),
                (b"x-forwarded-access-token", b"spoofed-token"),
            ]
        )
    )
    assert result == actor
    assert local.calls == 1
    assert prepared == ["local-user"]


def test_local_mode_requires_explicit_authenticator() -> None:
    with pytest.raises(ValueError, match="local_authenticator_required"):
        DatabricksIdentityResolver(trusted_ingress="local")


def test_settings_parse_explicit_values_and_refuse_secrets() -> None:
    binding = ResourceRef(name="fabricated-db", kind="postgres", scopes=("connect",))
    settings = DatabricksAppSettings.from_mapping(
        {
            "DATABRICKS_APP_PORT": "8000",
            "TETHER_SHUTDOWN_TIMEOUT_SECONDS": "10",
            "TETHER_RESOURCE_BINDINGS": '[{"name":"fabricated-db","kind":"postgres",'
            '"scopes":["connect"]}]',
            "TETHER_DATABASE_SOURCE_ID": "fabricated-source",
            "TETHER_DATABASE_SCHEMA": "fabricated_schema",
            "TETHER_DATABASE_ROLE_MODE": "caller",
            "TETHER_DATABASE_TLS_MODE": "required",
            "TETHER_DATABASE_MIGRATION_OWNER": "operator",
            "TETHER_DATABASE_CAPABILITY_PROFILE": "pg18-local",
        },
        trusted_ingress="databricks-apps",
    )
    assert settings.host == "0.0.0.0"  # noqa: S104
    assert settings.resource_bindings == (binding,)
    assert settings.database_profile is not None
    assert settings.database_profile.schema_name == "fabricated_schema"
    with pytest.raises(ValueError, match="secret_settings"):
        DatabricksAppSettings.from_mapping(
            {
                "DATABRICKS_APP_PORT": "8000",
                "TETHER_SHUTDOWN_TIMEOUT_SECONDS": "10",
                "DATABASE_PASSWORD": "never-print-this",
            },
            trusted_ingress="databricks-apps",
        )
    with pytest.raises(ValueError, match="unknown_app_setting"):
        DatabricksAppSettings.from_mapping(
            {
                "DATABRICKS_APP_PORT": "8000",
                "TETHER_SHUTDOWN_TIMEOUT_SECONDS": "10",
                "TETHER_UNREVIEWED_SETTING": "ignored",
            },
            trusted_ingress="databricks-apps",
        )


@pytest.mark.anyio
async def test_resource_provider_acquires_only_exact_bindings_and_closes_owned_only() -> None:
    binding = ResourceRef(name="fabricated-db", kind="postgres")
    owned, borrowed = Lease(object(), owned=True), Lease(object(), owned=False)
    calls: list[ResourceRef] = []

    async def acquire(ref: ResourceRef) -> ResourceLease:
        calls.append(ref)
        return owned if ref == binding else borrowed

    resources = DatabricksResources.from_bindings((binding,), acquire=acquire)
    assert await resources.acquire(binding) is owned
    with pytest.raises(PermissionError, match="resource_not_declared"):
        await resources.acquire(ResourceRef(name="other", kind="postgres"))
    assert calls == [binding]
    assert await resources.aclose() == ()
    assert await resources.aclose() == ()
    assert owned.closed == 1
    assert borrowed.closed == 0


@pytest.mark.anyio
async def test_resource_lifecycle_retains_timed_out_closer_and_reports_completion() -> None:
    entered, finish = asyncio.Event(), asyncio.Event()

    async def close_resource() -> None:
        entered.set()
        await finish.wait()

    lifecycle = ResourceLifecycle({"host": close_resource})
    first = await lifecycle.close(timeout_seconds=0.01)
    assert not first.complete
    assert first.outstanding == ("host",)
    assert await entered.wait()
    finish.set()
    second = await lifecycle.close(timeout_seconds=1)
    assert second == ShutdownReport(complete=True, closed=("host",))


class FakeASGI:
    def __init__(self, label: str) -> None:
        self.label = label
        self.paths: list[str] = []
        self.events: list[str] = []

    async def __call__(self, scope: Mapping[str, object], receive: Any, send: Any) -> None:
        if scope.get("type") == "lifespan":
            while True:
                message = await receive()
                self.events.append(str(message.get("type")))
                if message.get("type") == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message.get("type") == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        else:
            path = scope.get("path")
            self.paths.append(str(path))
            await send({"type": self.label, "path": path})


@pytest.mark.anyio
async def test_composition_routes_one_host_and_closes_child_lifespans() -> None:
    binding = ResourceRef(name="fabricated-resource", kind="test")
    settings = DatabricksAppSettings.from_values(
        trusted_ingress="local",
        host="127.0.0.1",
        port=8000,
        path_prefix="/app",
        shutdown_timeout_seconds=2,
        resource_bindings=(binding,),
    )
    local = LocalAuthenticator(Actor(id="local-user"))
    auth = DatabricksIdentityResolver(trusted_ingress="local", local_authenticator=local)
    resources = DatabricksResources.from_bindings(
        (binding,), acquire=lambda _ref: _lease(Lease(object(), owned=False))
    )
    api, ui = FakeASGI("api"), FakeASGI("ui")
    observed: dict[str, object] = {}

    def api_factory(*, clients: object, authenticator: object, settings: ApiSettings) -> FakeASGI:
        observed["api_prefix"] = settings.api_prefix
        observed["authenticator"] = authenticator
        return api

    def ui_factory(*, settings: UiSettings) -> FakeASGI:
        observed["ui_mount"] = settings.mount_path
        observed["discovery"] = settings.api_discovery_path
        return ui

    lifecycle = ResourceLifecycle({})
    app = create_carrier_app(
        settings=settings,
        client_provider=cast(ClientProvider, lambda actor: actor),
        authenticator=auth,
        resource_provider=resources,
        api_factory=cast(ApiFactory, api_factory),
        api_settings=ApiSettings(),
        ui_factory=ui_factory,
        ui_settings=UiSettings(),
        lifecycle=lifecycle,
    )
    assert isinstance(app, CarrierASGI)
    assert observed == {
        "api_prefix": "/app/api/v1",
        "authenticator": auth,
        "ui_mount": "/app",
        "discovery": "/app/api/v1/capabilities",
    }
    output: list[Mapping[str, object]] = []

    async def receive() -> Mapping[str, object]:
        return {"type": "http.request"}

    async def send(message: Mapping[str, object]) -> None:
        output.append(message)

    await app(
        {"type": "http", "http_version": "2", "path": "/app/api/v1/capabilities"},
        receive,
        send,
    )
    await app({"type": "http", "http_version": "2", "path": "/app/"}, receive, send)
    assert api.paths == ["/app/api/v1/capabilities"]
    assert ui.paths == ["/app/"]
    assert app.asgi_http_versions == {"2"}

    messages = asyncio.Queue[Mapping[str, object]]()
    await messages.put({"type": "lifespan.startup"})
    await messages.put({"type": "lifespan.shutdown"})

    async def lifespan_receive() -> Mapping[str, object]:
        return await messages.get()

    await app({"type": "lifespan"}, lifespan_receive, send)
    assert [item["type"] for item in output[-2:]] == [
        "lifespan.startup.complete",
        "lifespan.shutdown.complete",
    ]
    assert api.events == ["lifespan.startup", "lifespan.shutdown"]
    assert ui.events == ["lifespan.startup", "lifespan.shutdown"]
    assert app.shutdown_report == ShutdownReport(complete=True, closed=("api", "ui"))


async def _lease(value: ResourceLease) -> ResourceLease:
    return value
