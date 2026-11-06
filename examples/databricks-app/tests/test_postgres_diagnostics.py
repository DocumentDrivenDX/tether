from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import cast

import pytest
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
