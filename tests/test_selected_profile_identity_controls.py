"""Deterministic caller identity and revocation controller tests."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest
import selected_profiles_identity as identity
import selected_profiles_identity_credentials as identity_credentials
import selected_profiles_logs as logs
from databricks.sdk import WorkspaceClient
from selected_profiles_pg_oracle import OracleResult


def _fake_value(*parts: str) -> str:
    return "-".join(parts)


def _as_workspace(value: Any) -> WorkspaceClient:
    return cast(WorkspaceClient, value)


def _authorization(token: str) -> str:
    return " ".join(("Bearer", token))


def _jwt_authorization(marker: str) -> str:
    claims = json.dumps({"exp": int(time.time()) + 3600, "jti": marker}).encode()
    payload = base64.urlsafe_b64encode(claims).decode().rstrip("=")
    return _authorization(".".join(("fabricated", payload, marker)))


class SqlStateError(Exception):
    sqlstate: str

    def __init__(self, message: str, sqlstate: str) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


def test_identity_authorization_accepts_bearer_case_variants_and_rejects_absence() -> None:
    authorization = " ".join(("Bearer", _fake_value("fabricated", "token")))
    workspace = SimpleNamespace(
        config=SimpleNamespace(authenticate=lambda: {"authorization": authorization})
    )
    assert identity._authorization(_as_workspace(workspace)) == authorization
    workspace.config.authenticate = lambda: {"Authorization": "Basic fabricated"}
    with pytest.raises(RuntimeError, match="selected_profile_credential_missing"):
        identity._authorization(_as_workspace(workspace))


def test_auth_refusal_checks_missing_and_untrusted_credentials(monkeypatch) -> None:
    observed: list[tuple[str, Any]] = []

    class Response:
        status_code = 401

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            observed.append(("open", kwargs))

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            observed.append(("close", None))

        async def post(self, route: str, *, json: Any, headers: Any = None) -> Response:
            observed.append((route, headers))
            return Response()

    monkeypatch.setattr("httpx.AsyncClient", Client)
    asyncio.run(identity.auth_refusal("https://example.test", "/api/v1"))
    assert len([item for item in observed if item[0] == "/api/v1/sessions"]) == 2
    assert observed[-1] == ("close", None)


def test_auth_refusal_rejects_unexpected_success(monkeypatch) -> None:
    class Response:
        def __init__(self, status: int) -> None:
            self.status_code = status

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            self.responses = iter((Response(401), Response(200)))

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def post(self, *_args: Any, **_kwargs: Any) -> Response:
            return next(self.responses)

    monkeypatch.setattr("httpx.AsyncClient", Client)
    with pytest.raises(RuntimeError, match="untrusted_request_not_refused"):
        asyncio.run(identity.auth_refusal("https://example.test", "/api/v1"))


def test_validate_revocation_target_checks_owned_active_identity() -> None:
    class Current:
        def __init__(self, id: str) -> None:
            self.id = id

        def me(self) -> Any:
            return self

    class Principals:
        def get(self, principal: str) -> Any:
            return SimpleNamespace(id=principal, active=True, application_id="fabricated-app")

    service = SimpleNamespace(current_user=Current("fabricated-service"))
    admin = SimpleNamespace(
        current_user=Current("fabricated-admin"), service_principals=Principals()
    )
    result = asyncio.run(
        identity._validate_revocation_target(
            _as_workspace(admin),
            _as_workspace(service),
            "fabricated-service",
            "fabricated-app",
            "fabricated-runtime-app",
            "app",
        )
    )
    assert result == ("fabricated-service", "fabricated-app")


@pytest.mark.parametrize(
    ("service_id", "application_id", "admin_id", "active", "expected"),
    [
        ("other", "app", "admin", True, "revocation_target_not_owned_test_identity"),
        ("service", "runtime-app", "admin", True, "revocation_target_not_owned_test_identity"),
        ("service", "app", "admin", False, "revocation_target_identity_state_unexpected"),
    ],
)
def test_validate_revocation_target_rejects_unowned_or_inactive(
    service_id: str, application_id: str, admin_id: str, active: bool, expected: str
) -> None:
    class User:
        def __init__(self, id: str) -> None:
            self.id = id

        def me(self) -> Any:
            return self

    admin = SimpleNamespace(
        current_user=User(admin_id),
        service_principals=SimpleNamespace(
            get=lambda requested: SimpleNamespace(
                id=requested,
                active=active,
                application_id=application_id,
            )
        ),
    )
    service = SimpleNamespace(current_user=User("service"))
    with pytest.raises(RuntimeError, match=expected):
        asyncio.run(
            identity._validate_revocation_target(
                _as_workspace(admin),
                _as_workspace(service),
                service_id,
                application_id,
                "runtime-app",
                "app",
            )
        )


def test_restore_identity_retries_and_fails_after_bounded_attempts() -> None:
    class Principals:
        def __init__(self, succeed_at: int | None) -> None:
            self.attempts = 0
            self.succeed_at = succeed_at

        def patch(self, _id: str, **kwargs: Any) -> None:
            self.attempts += 1

        def get(self, _id: str) -> Any:
            return SimpleNamespace(
                id="fabricated",
                active=self.succeed_at == self.attempts,
                application_id="fabricated-app",
            )

    async def exercise(succeed_at: int | None) -> None:
        principals = Principals(succeed_at)
        admin = SimpleNamespace(service_principals=principals)
        if succeed_at is None:
            with pytest.raises(RuntimeError, match="test_identity_restore_failed"):
                await identity._restore_identity(_as_workspace(admin), "fabricated", [])
            assert principals.attempts == 3
        else:
            restored = await identity._restore_identity(_as_workspace(admin), "fabricated", [])
            assert restored.active is True
            assert principals.attempts == succeed_at

    asyncio.run(exercise(2))
    asyncio.run(exercise(None))


def test_revoke_restore_workflow_proves_denial_then_current_identity(monkeypatch, tmp_path) -> None:
    state = {"active": True}
    status_calls: list[tuple[str, int]] = []
    events: list[str] = []
    old_token = _jwt_authorization("old")
    fresh_token = _jwt_authorization("fresh")
    route_status = {
        old_token: iter((401, 401)),
        fresh_token: iter((401, 200)),
    }

    class Principals:
        def patch(self, principal: str, *, operations: Any, schemas: Any) -> None:
            assert principal == "fabricated-test-id"
            assert schemas
            state["active"] = operations[0].value["active"]

        def get(self, principal: str) -> Any:
            events.append("account-read")
            return SimpleNamespace(
                id=principal,
                active=state["active"],
                application_id="fabricated-test-app",
            )

    class CurrentUser:
        def __init__(self, principal: str) -> None:
            self.id = principal

        def me(self) -> Any:
            return self

    admin = SimpleNamespace(
        current_user=CurrentUser("fabricated-admin-id"), service_principals=Principals()
    )
    service = SimpleNamespace(
        current_user=CurrentUser("fabricated-test-id"),
        config=SimpleNamespace(
            host="https://example.test",
            authenticate=lambda: {"Authorization": old_token},
        ),
    )

    async def workspace_state(
        _host: str,
        _token: str,
        _principal: str,
        *,
        expected_active: bool,
        deadline: float | None = None,
    ):
        assert state["active"] is expected_active
        return "active" if expected_active else "inactive"

    async def status(
        _origin: str,
        _prefix: str,
        token: str,
        *,
        service: bool,
        timeout_seconds: float = 30,
    ) -> int:
        events.append("protected-route")
        status_calls.append((token, int(state["active"])))
        return next(route_status[token])

    async def fresh_access(
        refresh_credential: Any, host: str, principal: str, protected_status_for: Any
    ):
        credential = await refresh_credential()
        assert host == "https://example.test"
        assert principal == "fabricated-test-id"
        assert credential.host == host
        assert credential.authorization == fresh_token
        protected_status = protected_status_for(credential.authorization)
        await protected_status()
        await protected_status()
        return credential

    monkeypatch.setattr(identity, "_principal_status", status)
    monkeypatch.setattr(identity, "wait_for_workspace_identity", workspace_state)
    monkeypatch.setattr(identity, "fresh_workspace_access", fresh_access)
    reference = tmp_path / "identity.json"
    reference.write_text(
        json.dumps({"id": "fabricated-test-id", "application_id": "fabricated-test-app"})
    )
    observation = asyncio.run(
        identity.revoke_restore_test_identity(
            admin_workspace=_as_workspace(admin),
            service_workspace=_as_workspace(service),
            identity_reference=reference,
            app_principal_id="fabricated-runtime-app",
            app_name="fabricated-app",
            origin="https://example.test",
            prefix="/api/v1",
            refresh_credential=lambda: _fresh_credential("https://example.test", fresh_token),
        )
    )
    assert state["active"] is True
    assert observation == {"revocation_token_unexpired_at_denial": True}
    assert status_calls == [
        (old_token, 0),
        (fresh_token, 1),
        (fresh_token, 1),
    ]
    assert events[-2:] == ["protected-route", "account-read"]
    assert (
        asyncio.run(
            status(
                "https://example.test",
                "/api/v1",
                old_token,
                service=True,
            )
        )
        == 401
    )
    assert status_calls[-1] == (old_token, 1)


def test_restore_requires_fresh_account_active_read_after_workspace_success(monkeypatch) -> None:
    reads = 0
    old_token = _jwt_authorization("old")
    fresh_token = _jwt_authorization("fresh")

    class Admin:
        def operator(self) -> Any:
            return SimpleNamespace(
                id="fabricated-admin",
                user_name="admin@example.test",
                account_admin=True,
                workspace_id=1,
            )

        def get_service_principal(self, principal: str) -> Any:
            nonlocal reads
            reads += 1
            return SimpleNamespace(
                id=principal,
                active=False,
                application_id="fabricated-test-app",
            )

        def set_service_principal_active(self, _principal: str, active: bool) -> None:
            assert type(active) is bool

    service = SimpleNamespace(
        current_user=SimpleNamespace(me=lambda: SimpleNamespace(id="fabricated-test-id")),
        config=SimpleNamespace(
            host="https://example.test",
            authenticate=lambda: {"Authorization": fresh_token},
        ),
    )

    async def workspace_state(
        _host: str,
        _token: str,
        _principal: str,
        *,
        expected_active: bool,
        deadline: float | None = None,
    ) -> str:
        return "active" if expected_active else "inactive"

    status_calls = 0

    async def status(
        _origin: str,
        _prefix: str,
        _token: str,
        *,
        service: bool,
        timeout_seconds: float = 30,
    ) -> int:
        nonlocal status_calls
        status_calls += 1
        return 403 if status_calls == 1 else 200

    async def fresh_access(
        refresh_credential: Any, host: str, principal: str, protected_status_for: Any
    ):
        credential = await refresh_credential()
        assert host == "https://example.test"
        assert principal == "fabricated-test-id"
        assert credential.host == host
        assert credential.authorization == fresh_token
        assert await protected_status_for(credential.authorization)() == 200
        return credential

    async def restore_candidate(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(
            id="fabricated-test-id",
            active=True,
            application_id="fabricated-test-app",
        )

    monkeypatch.setattr(identity, "wait_for_workspace_identity", workspace_state)
    monkeypatch.setattr(identity, "fresh_workspace_access", fresh_access)
    monkeypatch.setattr(identity, "_principal_status", status)
    monkeypatch.setattr(identity, "_restore_identity", restore_candidate)
    with pytest.raises(RuntimeError, match="test_identity_restore_not_observed"):
        asyncio.run(
            identity._deactivate_check_and_restore(
                _as_workspace(Admin()),
                _as_workspace(service),
                "fabricated-test-id",
                "fabricated-test-app",
                old_token,
                "https://example.test",
                "/api/v1",
                [],
                lambda: _fresh_credential("https://example.test", fresh_token),
            )
        )
    assert reads == 2


def test_deactivate_workflow_restores_when_denial_check_fails(monkeypatch) -> None:
    state = {"active": True}

    class Principals:
        def patch(self, _principal: str, *, operations: Any, schemas: Any) -> None:
            state["active"] = operations[0].value["active"]

        def get(self, _principal: str) -> Any:
            return SimpleNamespace(
                id=_principal,
                active=state["active"],
                application_id="fabricated-test-app",
            )

    admin = SimpleNamespace(service_principals=Principals())
    service = SimpleNamespace(
        current_user=SimpleNamespace(me=lambda: SimpleNamespace(id="fabricated-test-id")),
        config=SimpleNamespace(
            host="https://example.test",
            authenticate=lambda: {
                "Authorization": _authorization(_fake_value("fabricated", "fresh-token"))
            },
        ),
    )

    async def workspace_state(
        _host: str,
        _token: str,
        _principal: str,
        *,
        expected_active: bool,
        deadline: float | None = None,
    ):
        return "active" if expected_active else "inactive"

    async def status(*_args: Any, **_kwargs: Any) -> int:
        return 200

    monkeypatch.setattr(identity, "_principal_status", status)
    monkeypatch.setattr(identity, "wait_for_workspace_identity", workspace_state)

    async def fresh_access(
        refresh_credential: Any, _host: str, _principal: str, protected_status_for: Any
    ):
        credential = await refresh_credential()
        await protected_status_for(credential.authorization)()
        return credential

    monkeypatch.setattr(identity, "fresh_workspace_access", fresh_access)
    with pytest.raises(RuntimeError, match="inactive_identity_still_authorized"):
        asyncio.run(
            identity._deactivate_check_and_restore(
                _as_workspace(admin),
                _as_workspace(service),
                "fabricated-test-id",
                "fabricated-test-app",
                _jwt_authorization("old"),
                "https://example.test",
                "/api/v1",
                [],
                lambda: _fresh_credential(
                    "https://example.test", _authorization(_fake_value("fabricated", "fresh-token"))
                ),
            )
        )
    assert state["active"] is True


def test_principal_separation_checks_both_callers_and_database_identity(monkeypatch) -> None:
    observed: list[tuple[str, bool]] = []

    class User:
        def __init__(self, principal: str, username: str) -> None:
            self.id, self.user_name = principal, username

        def me(self) -> Any:
            return self

    class Workspace:
        def __init__(self, principal: str, username: str, token: str) -> None:
            self.current_user = User(principal, username)
            self.config = SimpleNamespace(authenticate=lambda: {"Authorization": token})

    human = Workspace(
        "fabricated-human-id",
        "human@example.test",
        _authorization(_fake_value("human", "token")),
    )
    service = Workspace(
        "fabricated-service-id",
        "fabricated-service-user",
        _authorization(_fake_value("service", "token")),
    )

    async def status(_origin: str, _prefix: str, token: str, *, service: bool) -> int:
        observed.append((token, service))
        return 200

    async def database(*args: Any) -> tuple[str, str]:
        observed.append(("database-role-check", True))
        return "28P01", "fabricated-driver-1.0"

    monkeypatch.setattr(identity, "_principal_status", status)
    monkeypatch.setattr(identity, "_wrong_role_database_refusal", database)
    asyncio.run(
        identity.principal_separation(
            _as_workspace(human),
            _as_workspace(service),
            "fabricated-infra-id",
            "fabricated-infra-id",
            "https://example.test",
            "/api/v1",
            SimpleNamespace(),
            "fabricated-service-db-role",
        )
    )
    assert observed == [
        (" ".join(("Bearer", _fake_value("human", "token"))), False),
        (" ".join(("Bearer", _fake_value("service", "token"))), True),
        ("database-role-check", True),
    ]


@pytest.mark.parametrize(
    ("app_id", "infra_id", "human_id", "service_id", "expected"),
    [
        ("", "infra", "human", "service", "app_and_caller_principals_not_separate"),
        ("infra", "other", "human", "service", "app_and_caller_principals_not_separate"),
        ("infra", "infra", "human", "human", "selected_callers_not_distinct"),
    ],
)
def test_principal_separation_rejects_identity_collisions(
    monkeypatch, app_id: str, infra_id: str, human_id: str, service_id: str, expected: str
) -> None:
    class User:
        def __init__(self, principal: str) -> None:
            self.id = principal
            self.user_name = principal

        def me(self) -> Any:
            return self

    first = SimpleNamespace(current_user=User(human_id))
    second = SimpleNamespace(current_user=User(service_id))
    with pytest.raises(RuntimeError, match=expected):
        asyncio.run(
            identity.principal_separation(
                _as_workspace(first),
                _as_workspace(second),
                app_id,
                infra_id,
                "https://example.test",
                "/api/v1",
                SimpleNamespace(),
                "service-role",
            )
        )


def test_wrong_role_database_refusal_classifies_authentication_and_closes(monkeypatch) -> None:
    calls: list[str] = []

    async def refused(**arguments: Any) -> OracleResult:
        role = arguments["user"]
        calls.append(role)
        return (
            OracleResult("connected", current_user=role)
            if role == "caller"
            else OracleResult("auth_refused", sqlstate="28P01")
        )

    monkeypatch.setattr(identity, "probe_database", refused)
    workspace = SimpleNamespace(
        postgres=SimpleNamespace(
            generate_database_credential=lambda *, endpoint: SimpleNamespace(
                token=_fake_value("fabricated", "db")
            )
        )
    )
    settings = SimpleNamespace(
        pg_endpoint="endpoint", pg_host="host", pg_database="database", ca_file="ca"
    )

    async def run() -> None:
        await identity._wrong_role_database_refusal(
            _as_workspace(workspace), settings, "caller", "infra"
        )
        with pytest.raises(RuntimeError, match="database_principal_roles_not_separate"):
            await identity._wrong_role_database_refusal(
                _as_workspace(workspace), settings, "same", "same"
            )

        async def accepted(**arguments: Any) -> OracleResult:
            calls.append(arguments["user"])
            return OracleResult("connected", current_user=arguments["user"])

        monkeypatch.setattr(identity, "probe_database", accepted)
        with pytest.raises(RuntimeError, match="caller_token_accepted_as_infrastructure_role"):
            await identity._wrong_role_database_refusal(
                _as_workspace(workspace), settings, "caller", "infra"
            )
        assert calls == ["caller", "infra", "caller", "infra"]

    asyncio.run(run())


def test_log_follower_cli_cleanup_and_refusal_paths(monkeypatch) -> None:
    class Process:
        def __init__(self) -> None:
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self.stdout.feed_data(b"fabricated log line\n")
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.returncode: int | None = None
            self.terminated = False

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = 0

        def kill(self) -> None:
            self.returncode = -9

        async def wait(self) -> int:
            self.returncode = self.returncode or 0
            return self.returncode

    async def run() -> None:
        calls: list[tuple[Any, ...]] = []
        process = Process()

        async def create(*args: Any, **kwargs: Any) -> Process:
            calls.append(args)
            assert kwargs["stdout"] == asyncio.subprocess.PIPE
            return process

        monkeypatch.setattr(logs.shutil, "which", lambda _name: "/fabricated/bin/databricks")
        monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
        follower = await logs._start_follower("profile", "app", "APP")
        await follower.ready.wait()
        assert follower.ready.is_set()
        stdout, stderr = await logs._stop_follower(follower)
        assert b"fabricated log line" in stdout
        assert stderr == b""
        assert process.terminated is True
        assert "env" in calls[0][0]
        assert logs._clean_environment("/home/fabricated")[-1].startswith("PATH=")

        monkeypatch.setattr(logs.shutil, "which", lambda _name: None)
        with pytest.raises(RuntimeError, match="platform_log_cli_missing"):
            await logs._start_follower("profile", "app", "SYSTEM")

    asyncio.run(run())


def test_log_follower_failure_to_start_closes_pipe_resources(monkeypatch) -> None:
    class Process:
        def __init__(self, *, exited: bool = False, no_pipes: bool = False) -> None:
            self.stdout = None if no_pipes else asyncio.StreamReader()
            self.stderr = None if no_pipes else asyncio.StreamReader()
            if self.stdout is not None:
                self.stdout.feed_eof()
                assert self.stderr is not None
                self.stderr.feed_eof()
            self.returncode = 1 if exited else None
            self.killed = False

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        def terminate(self) -> None:
            self.returncode = 0

        async def wait(self) -> int:
            return self.returncode or 0

    async def exercise() -> None:
        monkeypatch.setattr(logs.shutil, "which", lambda _name: "/fabricated/databricks")
        exited = Process(exited=True)

        async def exited_cli(*_args: Any, **_kwargs: Any) -> Process:
            return exited

        monkeypatch.setattr(asyncio, "create_subprocess_exec", exited_cli)
        with pytest.raises(RuntimeError, match="platform_log_subscription_did_not_start"):
            await logs._start_follower("profile", "app", "APP")

        no_pipes = Process(no_pipes=True)

        async def pipe_cli(*_args: Any, **_kwargs: Any) -> Process:
            return no_pipes

        monkeypatch.setattr(asyncio, "create_subprocess_exec", pipe_cli)
        with pytest.raises(RuntimeError, match="platform_log_pipe_unavailable"):
            await logs._start_follower("profile", "app", "SYSTEM")
        assert no_pipes.killed is True

    asyncio.run(exercise())


async def _fresh_credential(host: str, authorization: str) -> identity_credentials.FreshCredential:
    return identity_credentials.FreshCredential(host, authorization)
