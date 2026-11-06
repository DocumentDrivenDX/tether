"""Deterministic credential-expiry controller tests."""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
import selected_profiles_expiry as expiry
import selected_profiles_identity_credentials as identity_credentials
from databricks.sdk import WorkspaceClient
from selected_profiles_pg_oracle import OracleResult


def _fake_value(*parts: str) -> str:
    return "-".join(parts)


def _authorization(token: str) -> str:
    return " ".join(("Bearer", token))


class SqlStateError(RuntimeError):
    sqlstate: str

    def __init__(self, message: str, sqlstate: str) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


def test_expiry_token_parser_and_database_credential_validation() -> None:
    payload = (
        base64.urlsafe_b64encode(json.dumps({"exp": 2_000_000_000}).encode()).decode().rstrip("=")
    )
    token = _authorization(
        _fake_value("fabricated", "header") + "." + payload + "." + _fake_value("signature")
    )
    assert expiry._token_expiry(token) == 2_000_000_000
    for malformed in (_authorization(_fake_value("no", "dots")),):
        with pytest.raises(RuntimeError, match="oauth_token_expiry_unavailable"):
            expiry._token_expiry(malformed)
    missing_expiry = base64.urlsafe_b64encode(b"{}").decode().rstrip("=")
    with pytest.raises(RuntimeError, match="oauth_token_expiry_unavailable"):
        expiry._token_expiry(
            _authorization(
                _fake_value("header") + "." + missing_expiry + "." + _fake_value("signature")
            )
        )
    bool_payload = base64.urlsafe_b64encode(json.dumps({"exp": True}).encode()).decode().rstrip("=")
    with pytest.raises(RuntimeError, match="oauth_token_expiry_unavailable"):
        expiry._token_expiry(
            _authorization(
                _fake_value("header") + "." + bool_payload + "." + _fake_value("signature")
            )
        )

    class Postgres:
        def __init__(self, credential: Any) -> None:
            self.credential = credential

        def generate_database_credential(self, *, endpoint: str) -> Any:
            assert endpoint == "fabricated-endpoint"
            return self.credential

    async def check(credential: Any, expected: Any) -> None:
        workspace = SimpleNamespace(postgres=Postgres(credential))
        if expected:
            assert (
                await expiry._database_credential(
                    cast(WorkspaceClient, workspace), "fabricated-endpoint"
                )
                == expected
            )
        else:
            with pytest.raises(RuntimeError, match="database_credential_expiry_unavailable"):
                await expiry._database_credential(
                    cast(WorkspaceClient, workspace), "fabricated-endpoint"
                )

    db_token = _fake_value("fabricated", "db", "token")
    asyncio.run(
        check(
            SimpleNamespace(token=db_token, expire_time=SimpleNamespace(seconds=55)),
            (db_token, 55),
        )
    )
    asyncio.run(check(SimpleNamespace(token=None, expire_time=None), None))


def test_open_database_builds_verified_tls_connection_and_closes(monkeypatch) -> None:
    calls: list[Any] = []

    class Connection:
        async def close(self) -> None:
            calls.append("closed")

    async def connect(conninfo: str) -> Connection:
        calls.append(conninfo)
        return Connection()

    monkeypatch.setattr(expiry.AsyncConnection, "connect", connect)
    settings = SimpleNamespace(
        pg_host="db.example.test", pg_database="fabricated-db", ca_file="/fabricated/ca.pem"
    )
    asyncio.run(
        expiry._open_database(
            settings, _fake_value("fabricated", "user"), _fake_value("fabricated", "db", "password")
        )
    )
    assert len(calls) == 2
    assert "sslmode=verify-full" in calls[0]
    assert "sslrootcert=/fabricated/ca.pem" in calls[0]
    assert calls[1] == "closed"


def test_after_expiry_requires_fresh_db_control_before_expired_credential(monkeypatch) -> None:
    fake_now = 2_000_000_000
    monkeypatch.setattr(expiry.time, "time", lambda: fake_now)
    fresh_db = _fake_value("fabricated", "fresh", "db")
    stale_db = _fake_value("fabricated", "stale", "db")

    async def protected(_origin: str, _prefix: str, _token: str) -> int:
        return 401 if _token == old_token else 200

    old_token = _fake_value("fabricated", "oauth", "expired")
    fresh_credential = identity_credentials.FreshCredential(
        "https://example.test", _authorization(_fake_value("fabricated", "oauth", "fresh"))
    )
    static_workspace = SimpleNamespace(
        current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name="fabricated-user"))
    )
    refresh_calls: list[str] = []

    async def refresh() -> identity_credentials.FreshCredential:
        refresh_calls.append("fresh")
        return fresh_credential

    db_calls: list[str] = []

    async def credential(_workspace: Any, _endpoint: str) -> tuple[str, int]:
        return fresh_db, 2_000_000_100

    oracle_calls: list[tuple[str, str]] = []

    async def oracle(**arguments: str | int) -> OracleResult:
        oracle_calls.append((str(arguments["user"]), str(arguments["password"])))
        return OracleResult("auth_refused", sqlstate="28P01")

    async def open_db(_settings: Any, _username: str, password: str) -> None:
        db_calls.append(password)
        if password == stale_db:
            raise SqlStateError("fabricated expired auth", "28P01")

    monkeypatch.setattr(expiry, "_protected_route", protected)
    monkeypatch.setattr(expiry, "_database_credential", credential)
    monkeypatch.setattr(expiry, "_open_database", open_db)
    monkeypatch.setattr(expiry, "probe_database", oracle)
    monkeypatch.setattr(expiry, "workspace_from_credential", lambda _credential: static_workspace)
    workspace = SimpleNamespace()
    settings = SimpleNamespace(
        pg_endpoint="fabricated-endpoint",
        pg_host="db.example.test",
        pg_database="fabricated-db",
        ca_file="/fabricated/ca.pem",
    )
    observed, status, sqlstate, driver_version = asyncio.run(
        expiry._after_expiry(
            cast(WorkspaceClient, workspace),
            settings,
            "https://example.test",
            "/api/v1",
            old_token,
            stale_db,
            fake_now - 6,
            "fabricated-user",
            refresh,
        )
    )
    assert status == 401
    assert observed == fake_now
    assert db_calls == [fresh_db]
    assert refresh_calls == ["fresh"]
    assert oracle_calls == [("fabricated-user", stale_db)]
    assert sqlstate == "28P01"
    assert driver_version == "0.32.0"


def test_after_expiry_rejects_transport_failure_and_still_checks_oauth(monkeypatch) -> None:
    calls: list[str] = []
    fresh_db = _fake_value("fabricated", "fresh")
    stale_db = _fake_value("fabricated", "stale")

    old_token = _fake_value("fabricated", "oauth", "expired")

    async def protected(_origin: str, _prefix: str, token: str) -> int:
        calls.append("oauth")
        return 403 if token == old_token else 200

    async def credential(_workspace: Any, _endpoint: str) -> tuple[str, int]:
        return fresh_db, 2_000_000_000

    async def oracle(**_arguments: str | int) -> OracleResult:
        return OracleResult("other_failure")

    fresh = identity_credentials.FreshCredential(
        "https://example.test", _authorization(_fake_value("fabricated", "fresh", "oauth"))
    )
    static_workspace = SimpleNamespace(
        current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name="user"))
    )

    async def refresh() -> identity_credentials.FreshCredential:
        return fresh

    async def open_db(_settings: Any, _username: str, password: str) -> None:
        calls.append(password)
        if password == stale_db:
            raise ConnectionError("offline")

    monkeypatch.setattr(expiry, "_protected_route", protected)
    monkeypatch.setattr(expiry, "_database_credential", credential)
    monkeypatch.setattr(expiry, "_open_database", open_db)
    monkeypatch.setattr(expiry, "probe_database", oracle)
    monkeypatch.setattr(expiry, "workspace_from_credential", lambda _credential: static_workspace)
    with pytest.raises(RuntimeError, match="expired_credential_still_accepted"):
        asyncio.run(
            expiry._after_expiry(
                cast(WorkspaceClient, SimpleNamespace()),
                SimpleNamespace(
                    pg_endpoint="endpoint",
                    pg_host="db.example.test",
                    pg_database="fabricated-db",
                    ca_file="/fabricated/ca.pem",
                ),
                "https://example.test",
                "/api/v1",
                old_token,
                stale_db,
                int(expiry.time.time()) - 5,
                "user",
                refresh,
            )
        )
    assert calls == ["oauth", "oauth", fresh_db]


def test_credential_rotation_runs_expiry_and_fresh_credential_control(monkeypatch) -> None:
    events: list[str] = []
    now = 2_000_000_000
    database_token = _fake_value("fabricated", "database")
    fresh_database = _fake_value("fabricated", "database", "fresh")
    jwt_payload = (
        base64.urlsafe_b64encode(json.dumps({"exp": now + 10}).encode()).decode().rstrip("=")
    )
    token = _authorization(_fake_value("a") + "." + jwt_payload + "." + _fake_value("c"))

    class Workspace:
        current_user = SimpleNamespace(me=lambda: SimpleNamespace(user_name="fabricated-db-user"))
        config = SimpleNamespace(authenticate=lambda: {"Authorization": token})
        postgres = SimpleNamespace(
            generate_database_credential=lambda *, endpoint: SimpleNamespace(
                token=database_token, expire_time=SimpleNamespace(seconds=now + 20)
            )
        )

    refreshed = SimpleNamespace(
        current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name="fabricated-db-user")),
        postgres=SimpleNamespace(
            generate_database_credential=lambda *, endpoint: SimpleNamespace(
                token=fresh_database, expire_time=SimpleNamespace(seconds=now + 200)
            )
        ),
    )

    async def after(*args: Any) -> tuple[int, int, str, str]:
        events.append("expired")
        assert args[-3] == now + 20
        await args[-1]()
        return now + 21, 401, "28P01", "0.32.0"

    refresh_count = 0

    async def refresh() -> identity_credentials.FreshCredential:
        nonlocal refresh_count
        refresh_count += 1
        return identity_credentials.FreshCredential(
            "https://example.test", _authorization(_fake_value("fresh", "oauth"))
        )

    async def open_db(_settings: Any, _username: str, password: str) -> None:
        events.append(password)

    async def route(_origin: str, _prefix: str, authorization: str) -> int:
        events.append("route-fresh" if authorization != token else "route-old")
        return 200

    monkeypatch.setattr(expiry.time, "time", lambda: now)
    monkeypatch.setattr(expiry, "_after_expiry", after)
    monkeypatch.setattr(expiry, "_open_database", open_db)
    monkeypatch.setattr(expiry, "_protected_route", route)
    monkeypatch.setattr(expiry, "workspace_from_credential", lambda _credential: refreshed)
    result = asyncio.run(
        expiry.credential_rotation(
            cast(WorkspaceClient, Workspace()),
            SimpleNamespace(
                pg_endpoint="endpoint",
                pg_host="db.example.test",
                pg_database="fabricated-db",
                ca_file="/fabricated/ca.pem",
            ),
            "https://example.test",
            "/api",
            refresh_credential=refresh,
        )
    )
    assert result["expired_credential_rejected"] == 1
    assert result["fresh_credentials_accepted"] == 1
    assert result["database_credential_refusal_sqlstate"] == "28P01"
    assert result["database_oracle_driver_version"] == "0.32.0"
    assert "expired" in events
    assert fresh_database in events
    assert refresh_count == 2


def test_credential_rotation_refuses_missing_identity_or_failed_fresh_probe(monkeypatch) -> None:
    class Workspace:
        current_user = SimpleNamespace(me=lambda: SimpleNamespace(user_name=""))

    async def invalid_refresh() -> identity_credentials.FreshCredential:
        raise AssertionError("refresh must not run before initial identity validation")

    with pytest.raises(RuntimeError, match="database_caller_identity_unavailable"):
        asyncio.run(
            expiry.credential_rotation(
                cast(WorkspaceClient, Workspace()),
                SimpleNamespace(),
                "https://example.test",
                "/api",
                refresh_credential=invalid_refresh,
            )
        )
