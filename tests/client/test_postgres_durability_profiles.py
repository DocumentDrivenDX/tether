from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from tether.client import Actor
from tether.client.postgres import (
    NATIVE_POSTGRES_PROFILE,
    PostgresDurabilityProfile,
    PostgresResources,
)
from tether.types.storage import IntegrityError


def test_managed_profile_requires_a_bound_endpoint_id() -> None:
    with pytest.raises(ValueError, match="managed_profile_requires_endpoint"):
        PostgresDurabilityProfile("databricks-lakebase-pg18")
    with pytest.raises(ValueError, match="unknown_postgres_durability_profile"):
        PostgresDurabilityProfile("unrecognized")  # type: ignore[arg-type]


def test_managed_profile_rejects_an_unattested_connection_source_before_io() -> None:
    profile = PostgresDurabilityProfile("databricks-lakebase-pg18", "endpoint-a")
    calls = 0

    class UnattestedSource:
        durability_profile = PostgresDurabilityProfile("databricks-lakebase-pg18", "endpoint-b")

        @asynccontextmanager
        async def __call__(self, actor: Actor, session_id: str | None):
            nonlocal calls
            del actor, session_id
            calls += 1
            raise AssertionError("profile_mismatch_must_precede_connection")
            yield  # pragma: no cover

    source = UnattestedSource()

    async def scenario() -> None:
        with pytest.raises(IntegrityError, match="postgres_profile_attestation_mismatch"):
            await PostgresResources.open(
                source,
                actor=Actor(id="fabricated-infra"),
                schema="fabricated_durable_profile",
                durability_profile=profile,
            )
        if calls != 0:
            raise AssertionError("unattested_source_was_called")

    asyncio.run(scenario())


def test_native_profile_remains_the_default() -> None:
    if PostgresDurabilityProfile("native") != NATIVE_POSTGRES_PROFILE:
        raise AssertionError("native_profile_default_changed")
