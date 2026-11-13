from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
import threading
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
import tether_databricks_example.postgres as postgres_module
from databricks.sdk import errors as sdk_errors
from tether_databricks_example.postgres import (
    LakebaseConnectionSource,
    LakebaseSettings,
    WorkspacePostgresClient,
)

from tether.client import Actor, Credential


class _Credential:
    async def authorization(self) -> str:
        return " ".join(("Bearer", "fabricated-user-token"))


def _source(error: Exception) -> LakebaseConnectionSource:
    def fail(endpoint: str) -> object:
        del endpoint
        raise error

    client = cast(
        WorkspacePostgresClient,
        SimpleNamespace(postgres=SimpleNamespace(generate_database_credential=fail)),
    )
    return LakebaseConnectionSource(
        LakebaseSettings(
            workspace_host="https://workspace.invalid",
            endpoint="fabricated-endpoint",
            host="database.invalid",
            database="fabricated-database",
            infra_role="fabricated-infrastructure-role",
        ),
        database_user=lambda actor: actor.id,
        infrastructure_client=client,
        user_client_factory=lambda _host, _token: client,
    )


def test_mint_failure_logs_closed_metadata_without_exception_text(caplog) -> None:
    content_marker = "".join(("fabricated", "-sdk-", "message-", "marker"))

    class PermissionDeniedWithStatus(sdk_errors.PermissionDenied):
        status = "403"

    error = PermissionDeniedWithStatus(content_marker, error_code="PERMISSION_DENIED")
    source = _source(error)
    actor = Actor(id="fabricated-user", credential=cast(Credential, _Credential()))

    async def scenario() -> None:
        with pytest.raises(PermissionError, match="database_credential_unavailable"):
            async with source(actor, None):
                raise AssertionError("unreachable_connection")

    with caplog.at_level(logging.WARNING):
        asyncio.run(scenario())

    record = " ".join(record.getMessage() for record in caplog.records)
    expected = (
        "tether_lakebase_credential_failure phase=database_token_mint "
        "exception=permission_denied status=403 code=PERMISSION_DENIED"
    )
    if expected not in record:
        raise AssertionError("safe_mint_failure_metadata_missing")
    if content_marker in record or "fabricated-user-token" in record:
        raise AssertionError("exception_or_credential_text_logged")


def test_unknown_exception_metadata_is_reduced_to_other(caplog) -> None:
    content_marker = "".join(("fabricated", "-unknown-", "error-", "marker"))

    class UnlistedFailure(Exception):
        error_code: str
        status: str

        def __init__(self, message: str) -> None:
            super().__init__(message)
            self.error_code = "fabricated-private-code"
            self.status = "999"

    error = UnlistedFailure(content_marker)
    source = _source(error)
    actor = Actor(id="fabricated-user", credential=cast(Credential, _Credential()))

    async def scenario() -> None:
        with pytest.raises(PermissionError, match="database_credential_unavailable"):
            async with source(actor, None):
                raise AssertionError("unreachable_connection")

    with caplog.at_level(logging.WARNING):
        asyncio.run(scenario())

    record = " ".join(record.getMessage() for record in caplog.records)
    if (
        "phase=database_token_mint exception=other status=other code=other" not in record
        or content_marker in record
        or "fabricated-private-code" in record
        or "UnlistedFailure" in record
    ):
        raise AssertionError("unknown_exception_not_safely_reduced")


def test_empty_database_credential_is_a_distinct_safe_phase(caplog) -> None:
    client = cast(
        WorkspacePostgresClient,
        SimpleNamespace(
            postgres=SimpleNamespace(
                generate_database_credential=lambda endpoint: (
                    SimpleNamespace(token="") if endpoint == "fabricated-endpoint" else None
                )
            )
        ),
    )
    source = LakebaseConnectionSource(
        LakebaseSettings(
            workspace_host="https://workspace.invalid",
            endpoint="fabricated-endpoint",
            host="database.invalid",
            database="fabricated-database",
            infra_role="fabricated-infrastructure-role",
        ),
        database_user=lambda actor: actor.id,
        infrastructure_client=client,
        user_client_factory=lambda _host, _token: client,
    )
    actor = Actor(id="fabricated-user", credential=cast(Credential, _Credential()))

    async def scenario() -> None:
        with pytest.raises(PermissionError, match="database_credential_unavailable"):
            async with source(actor, None):
                raise AssertionError("unreachable_connection")

    with caplog.at_level(logging.WARNING):
        asyncio.run(scenario())
    if not any(
        "phase=database_token_empty exception=none status=none code=none" in record.getMessage()
        for record in caplog.records
    ):
        raise AssertionError("empty_credential_phase_missing")


@pytest.mark.parametrize("mint_fails", [False, True])
def test_mint_cancellation_retains_thread_through_repeated_cancellation(
    monkeypatch: pytest.MonkeyPatch, mint_fails: bool
) -> None:
    held_mint = _HeldMint(mint_fails)
    source, actor = held_mint.install(monkeypatch)
    asyncio.run(_assert_mint_cancellation(source, actor, held_mint, monkeypatch))


class _HeldMint:
    def __init__(self, fails: bool) -> None:
        self.fails = fails
        self.release = threading.Event()
        self.finished = threading.Event()
        self.connection_attempts: list[bool] = []
        self.loop: asyncio.AbstractEventLoop | None = None
        self.entered: asyncio.Event | None = None

    def generate(self, *, endpoint: str) -> object:
        if endpoint != "fabricated-endpoint" or self.loop is None or self.entered is None:
            raise AssertionError("unexpected_mint_setup")
        self.loop.call_soon_threadsafe(self.entered.set)
        try:
            self.release.wait()
            if self.fails:
                raise RuntimeError("fabricated mint failure")
            return SimpleNamespace(token="".join(("fabricated", "-minted-token")))
        finally:
            self.finished.set()

    def install(self, monkeypatch: pytest.MonkeyPatch) -> tuple[LakebaseConnectionSource, Actor]:
        control = self

        class FakeAsyncConnection:
            @staticmethod
            async def connect(connection_string: str) -> object:
                del connection_string
                control.connection_attempts.append(True)
                raise AssertionError("connection_started_before_mint_settlement")

        psycopg_module = ModuleType("psycopg")
        psycopg_module.AsyncConnection = FakeAsyncConnection  # type: ignore[attr-defined]
        psycopg_module.__path__ = []  # type: ignore[attr-defined]
        conninfo_module = ModuleType("psycopg.conninfo")
        conninfo_module.make_conninfo = lambda **_values: "fabricated-connection"  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "psycopg", psycopg_module)
        monkeypatch.setitem(sys.modules, "psycopg.conninfo", conninfo_module)

        client = cast(
            WorkspacePostgresClient,
            SimpleNamespace(postgres=SimpleNamespace(generate_database_credential=self.generate)),
        )
        source = LakebaseConnectionSource(
            LakebaseSettings(
                workspace_host="https://workspace.invalid",
                endpoint="fabricated-endpoint",
                host="database.invalid",
                database="fabricated-database",
                infra_role="fabricated-infrastructure-role",
            ),
            database_user=lambda actor: actor.id,
            infrastructure_client=client,
            user_client_factory=lambda _host, _token: client,
        )
        actor = Actor(id="fabricated-user", credential=cast(Credential, _Credential()))
        return source, actor


async def _assert_mint_cancellation(
    source: LakebaseConnectionSource,
    actor: Actor,
    held_mint: _HeldMint,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    held_mint.loop = loop
    held_mint.entered = asyncio.Event()
    first_cancellation_seen = asyncio.Event()
    second_cancellation_seen = asyncio.Event()
    original_shield = asyncio.shield
    seen = 0

    async def observe_cancellation(task: asyncio.Future[Any]) -> Any:
        nonlocal seen
        try:
            return await original_shield(task)
        except asyncio.CancelledError:
            seen += 1
            if seen == 1:
                first_cancellation_seen.set()
            elif seen == 2:
                second_cancellation_seen.set()
            raise

    monkeypatch.setattr(postgres_module.asyncio, "shield", observe_cancellation)
    mint_task = asyncio.create_task(_open_source(source, actor))
    try:
        await _await_event_or_task(held_mint.entered, mint_task, "mint_ended_before_thread_entry")
        await _cancel_mint_and_assert_retained(
            mint_task,
            first_cancellation_seen,
            held_mint.finished,
            "mint_ended_before_first_cancellation_settlement",
            "first_cancellation_released_mint_ownership",
        )
        await _cancel_mint_and_assert_retained(
            mint_task,
            second_cancellation_seen,
            held_mint.finished,
            "mint_ended_before_second_cancellation_settlement",
            "repeated_cancellation_released_mint_ownership",
        )
        held_mint.release.set()
        with pytest.raises(asyncio.CancelledError):
            await mint_task
        if not held_mint.finished.is_set():
            raise AssertionError("mint_thread_not_finished_before_cancel_return")
        if held_mint.connection_attempts:
            raise AssertionError("connection_attempted_after_cancelled_mint")
    finally:
        held_mint.release.set()
        await _settle_owned_task(mint_task)
        if held_mint.entered.is_set() and not held_mint.finished.is_set():
            raise AssertionError("mint_thread_not_settled_before_source_return")


async def _cancel_mint_and_assert_retained(
    task: asyncio.Task[None],
    cancellation_seen: asyncio.Event,
    finished: threading.Event,
    terminal_reason: str,
    retained_reason: str,
) -> None:
    task.cancel()
    await _await_event_or_task(cancellation_seen, task, terminal_reason)
    if task.done() or finished.is_set():
        raise AssertionError(retained_reason)


async def _open_source(source: LakebaseConnectionSource, actor: Actor) -> None:
    async with source(actor, None):
        raise AssertionError("cancelled_mint_opened_connection")


async def _await_event_or_task(
    event: asyncio.Event, task: asyncio.Task[None], terminal_reason: str
) -> None:
    waiter = asyncio.create_task(event.wait())
    try:
        done, _ = await asyncio.wait((waiter, task), return_when=asyncio.FIRST_COMPLETED)
        if waiter not in done:
            task.result()
            raise AssertionError(terminal_reason)
    finally:
        if not waiter.done():
            waiter.cancel()
        await _settle_owned_task(waiter)


async def _settle_owned_task(task: asyncio.Task[Any]) -> None:
    cancellation: asyncio.CancelledError | None = None
    current = asyncio.current_task()
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            if current is None or current.cancelling() == 0:
                break
            if cancellation is None:
                cancellation = error
        except BaseException:
            break
    with contextlib.suppress(BaseException):
        task.result()
    if cancellation is not None:
        raise cancellation
