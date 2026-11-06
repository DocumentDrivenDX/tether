"""Deterministic public owner-isolation controller tests."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
import selected_profiles_owner as owner


def _fake_value(*parts: str) -> str:
    return "-".join(parts)


class RowPolicyError(RuntimeError):
    sqlstate: str

    def __init__(self, message: str, sqlstate: str) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


def _authorization(token: str) -> str:
    return " ".join(("Bearer", token))


def test_owner_api_isolation_returns_owned_sessions_and_closes_on_failure() -> None:
    from tether.client.remote import RemoteProblem

    class Client:
        def __init__(self, owner_name: str, fail_list: bool = False) -> None:
            self.owner_name = owner_name
            self.created: str | None = None
            self.fail_list = fail_list
            self.closed: list[str] = []

        async def create(self, *, ref: str) -> Any:
            self.created = self.owner_name + "-session"
            return SimpleNamespace(id=self.created)

        async def list(self, *, limit: int) -> Any:
            if self.fail_list:
                raise RuntimeError("fabricated-list-failure")
            return SimpleNamespace(items=[SimpleNamespace(id=self.created)])

        async def get(self, session_id: str) -> Any:
            if session_id != self.created:
                raise RemoteProblem(404, "not_found")
            return SimpleNamespace(summary=SimpleNamespace(id=session_id))

        async def close(self, session_id: str) -> None:
            if session_id != self.created:
                raise RemoteProblem(404, "not_found")
            self.closed.append(session_id)

    async def run() -> None:
        first, second = Client("one"), Client("two")
        left, right = await owner._api_owner_checks(first, second)
        assert (left.id, right.id) == ("one-session", "two-session")

        first, second = Client("one"), Client("two", fail_list=True)
        with pytest.raises(RuntimeError, match="fabricated-list-failure"):
            await owner._api_owner_checks(first, second)
        assert first.closed == ["one-session"]

    asyncio.run(run())


def test_owner_api_refusals_reject_readable_or_wrong_status_cross_owner() -> None:
    from tether.client.remote import RemoteProblem

    class Client:
        async def get(self, session_id: str) -> None:
            raise RemoteProblem(500, "server_error")

        async def close(self, session_id: str) -> None:
            return None

    async def run() -> None:
        with pytest.raises(RuntimeError, match="cross_owner_read_wrong_refusal"):
            await owner._deny_cross_owner_read(Client(), "foreign", RemoteProblem)
        with pytest.raises(RuntimeError, match="cross_owner_session_mutated"):
            await owner._deny_cross_owner_close(Client(), "foreign", RemoteProblem)

    asyncio.run(run())


class _RawCursor:
    def __init__(self, row: Any = None, rowcount: int = 0) -> None:
        self.row = row
        self.rowcount = rowcount

    async def fetchone(self) -> Any:
        return self.row


class _Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _RawConnection:
    def __init__(
        self,
        actor: str,
        visible: set[bytes],
        *,
        privileged: bool = False,
        update_count: int = 0,
        cross_insert_sqlstate: str | None = "42501",
        own_insert_count: int = 1,
    ) -> None:
        self.actor = actor.encode()
        self.visible = visible
        self.privileged = privileged
        self.update_count = update_count
        self.cross_insert_sqlstate = cross_insert_sqlstate
        self.own_insert_count = own_insert_count
        self.statements: list[str] = []

    def transaction(self) -> _Transaction:
        return _Transaction()

    async def execute(self, query: Any, params: tuple[Any, ...] | None = None) -> _RawCursor:
        statement = query.as_string(None) if hasattr(query, "as_string") else str(query)
        self.statements.append(statement)
        if statement.startswith("SELECT rolsuper"):
            return _RawCursor((self.privileged, self.privileged))
        if statement.startswith("SELECT count(*)"):
            return _RawCursor((int(params is not None and params[0] in self.visible),))
        if statement.startswith("INSERT"):
            assert params is not None
            if params[0] != self.actor and self.cross_insert_sqlstate is not None:
                raise RowPolicyError("fabricated row policy violation", self.cross_insert_sqlstate)
            return _RawCursor(rowcount=self.own_insert_count)
        if statement.startswith("UPDATE"):
            return _RawCursor(rowcount=self.update_count)
        raise AssertionError("unexpected fabricated SQL statement")


def _raw_source(connection: _RawConnection):
    @asynccontextmanager
    async def open_connection(_actor: Any, _session_id: str):
        yield connection

    return open_connection


def test_raw_owner_controls_cover_asymmetric_visibility_and_rollback_writes() -> None:
    async def run() -> None:
        human = SimpleNamespace(id="human-db-owner")
        service = SimpleNamespace(id="service-db-owner")
        human_row, service_row = b"human-session", b"service-session"
        human_db = _RawConnection(human.id, {human_row}, privileged=True)
        service_db = _RawConnection(service.id, {service_row})
        await owner._raw_owner_checks(
            (_raw_source(human_db), _raw_source(service_db)),
            (human, service),
            "fabricated_schema",
            (human_row.decode(), service_row.decode()),
        )
        assert any(statement.startswith("UPDATE") for statement in service_db.statements)
        assert sum(statement.startswith("INSERT") for statement in service_db.statements) == 2
        assert any("SELECT count(*)" in statement for statement in service_db.statements)

    asyncio.run(run())


def test_raw_owner_controls_reject_privileged_service_and_wrong_counts() -> None:
    async def run() -> None:
        human = SimpleNamespace(id="human-db-owner")
        service = SimpleNamespace(id="service-db-owner")
        human_row, service_row = b"human-session", b"service-session"
        with pytest.raises(RuntimeError, match="raw_rls_caller_not_nonadmin"):
            await owner._raw_owner_checks(
                (
                    _raw_source(_RawConnection(human.id, {human_row}, privileged=True)),
                    _raw_source(_RawConnection(service.id, {service_row}, privileged=True)),
                ),
                (human, service),
                "fabricated_schema",
                (human_row.decode(), service_row.decode()),
            )
        with pytest.raises(RuntimeError, match="raw_rls_owner_visibility_mismatch"):
            await owner._raw_owner_checks(
                (
                    _raw_source(_RawConnection(human.id, set())),
                    _raw_source(_RawConnection(service.id, {service_row})),
                ),
                (human, service),
                "fabricated_schema",
                (human_row.decode(), service_row.decode()),
            )

    asyncio.run(run())


def test_raw_mutation_controls_reject_unclassified_insert_and_update() -> None:
    async def run() -> None:
        actor = SimpleNamespace(id="service-owner")
        own = b"own"
        for connection, expected in (
            (
                _RawConnection(actor.id, {own}, cross_insert_sqlstate="23505"),
                "raw_rls_cross_owner_insert_unclassified",
            ),
            (
                _RawConnection(actor.id, {own}, update_count=1),
                "raw_rls_cross_owner_update_succeeded",
            ),
            (
                _RawConnection(actor.id, {own}, own_insert_count=0),
                "raw_rls_own_insert_failed",
            ),
            (
                _RawConnection(actor.id, {own}, cross_insert_sqlstate=None),
                "raw_rls_cross_owner_insert_succeeded",
            ),
        ):
            with pytest.raises(RuntimeError, match=expected):
                await owner._raw_mutation_checks(
                    _raw_source(connection), actor, "schema", "own", "foreign", "human"
                )

    asyncio.run(run())


def test_owner_credentials_and_remote_transport_cleanup(monkeypatch) -> None:
    calls: list[str] = []

    class Transport:
        def __init__(self, **kwargs: Any) -> None:
            calls.append("transport")

        async def aclose(self) -> None:
            calls.append("transport-close")

    class Client:
        @classmethod
        async def open(cls, settings: Any, *, http_client: Any) -> Any:
            calls.append("open")
            raise RuntimeError("fabricated-open-failure")

    monkeypatch.setattr("httpx.AsyncClient", Transport)
    monkeypatch.setattr("tether.client.remote.RemoteClient", Client)

    async def run() -> None:
        with pytest.raises(RuntimeError, match="fabricated-open-failure"):
            await owner._remote(
                "https://example.test",
                "/api",
                _authorization(_fake_value("fabricated", "token")),
                service=True,
            )
        token = _fake_value("fabricated", "token")
        credential = await owner._credential(token)
        assert await credential.authorization() == token

    asyncio.run(run())
    assert calls == ["transport", "open", "transport-close"]


def test_owner_api_accepts_public_concealed_and_denied_refusal_types() -> None:
    from tether.client import ExecutionDenied, UnknownSession
    from tether.client.remote import RemoteProblem

    class Client:
        def __init__(self, error: Exception) -> None:
            self.error = error

        async def get(self, session_id: str) -> None:
            raise self.error

        async def close(self, session_id: str) -> None:
            raise self.error

    async def run() -> None:
        for error in (UnknownSession("unknown_session"), ExecutionDenied("denied")):
            client = Client(error)
            await owner._deny_cross_owner_read(client, "foreign", RemoteProblem)
            await owner._deny_cross_owner_close(client, "foreign", RemoteProblem)
        for error, expected in (
            (RemoteProblem(500, "server_error"), "cross_owner_read_wrong_refusal"),
            (RemoteProblem(500, "server_error"), "cross_owner_mutation_wrong_refusal"),
            (RuntimeError("fabricated_unrelated_failure"), "cross_owner_read_wrong_refusal"),
        ):
            client = Client(error)
            check = (
                owner._deny_cross_owner_close
                if expected == "cross_owner_mutation_wrong_refusal"
                else owner._deny_cross_owner_read
            )
            with pytest.raises(RuntimeError, match=expected):
                await check(client, "foreign", RemoteProblem)

        class Readable:
            async def get(self, session_id: str) -> object:
                return object()

            async def close(self, session_id: str) -> None:
                return None

        with pytest.raises(RuntimeError, match="cross_owner_session_readable"):
            await owner._deny_cross_owner_read(Readable(), "foreign", RemoteProblem)
        with pytest.raises(RuntimeError, match="cross_owner_session_mutated"):
            await owner._deny_cross_owner_close(Readable(), "foreign", RemoteProblem)

    asyncio.run(run())
