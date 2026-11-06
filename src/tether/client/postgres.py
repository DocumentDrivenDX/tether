"""Optional PostgreSQL composition for public client embedders.

Importing this module does not load psycopg. The caller supplies an asynchronous
connection source and owns its pool, credentials, and role configuration.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, Self
from uuid import UUID

from tether.client import Actor, ProvidedStorage
from tether.stores.postgres.durability import (
    NATIVE_POSTGRES_PROFILE,
    PostgresDurabilityProfile,
)
from tether.types.storage import RecoveredTurn, StorageLimits

if TYPE_CHECKING:
    from psycopg import AsyncConnection

    from tether.stores.postgres.history import PostgresHistory
    from tether.stores.postgres.root import DatabaseRoot
    from tether.stores.postgres.store import PostgresSessionStore

    Connection = AsyncConnection[tuple[object, ...]]
else:
    Connection = object


class ConnectionSource(Protocol):
    """Caller-owned source of idle connections under an explicit actor."""

    def __call__(
        self, actor: Actor, session_id: str | None, /
    ) -> AbstractAsyncContextManager[Connection]: ...


class PostgresResources:
    """Own a Tether database root while borrowing the caller's connection source."""

    def __init__(
        self, root: DatabaseRoot, store: PostgresSessionStore, history: PostgresHistory
    ) -> None:
        self._root, self._store, self._history = root, store, history
        self._closed = False
        self._drained = False

    @classmethod
    async def open(
        cls,
        source: ConnectionSource,
        *,
        actor: Actor,
        schema: str,
        limits: StorageLimits | None = None,
        durability_profile: PostgresDurabilityProfile = NATIVE_POSTGRES_PROFILE,
    ) -> Self:
        """Verify an existing schema and open root/store/history without migrating it."""
        from tether.stores.postgres.history import PostgresHistory
        from tether.stores.postgres.root import DatabaseRoot
        from tether.stores.postgres.store import PostgresSessionStore

        root = await DatabaseRoot.open(
            source,
            actor=actor,
            schema=schema,
            limits=limits,
            durability_profile=durability_profile,
        )
        try:
            return cls(root, PostgresSessionStore(root), PostgresHistory(root))
        except BaseException:
            await root.aclose()
            raise

    @property
    def storage(self) -> ProvidedStorage:
        """Return ports for ClientHost; both are borrowed by that host."""
        if self._closed:
            raise RuntimeError("postgres_resources_closed")
        return ProvidedStorage(store=self._store, history=self._history)

    async def recover_owner(
        self, actor: Actor, *, now: datetime | None = None
    ) -> tuple[RecoveredTurn, ...]:
        """Recover only the current actor's expired work using current caller credentials."""
        if self._closed:
            raise RuntimeError("postgres_resources_closed")
        caller = self._store.for_actor(actor)
        return await caller.recover_owner(now=now or datetime.now(UTC))

    async def wait_closed(self) -> bool:
        """Await an initiated physical root drain before closing the borrowed source."""
        if not self._closed:
            raise RuntimeError("postgres_resources_not_closing")
        self._drained = await self._root.wait_closed()
        return self._drained

    async def aclose(self) -> bool:
        """Close the root; never close the borrowed source or its pool."""
        if self._drained:
            return True
        self._closed = True
        self._drained = await self._root.aclose()
        return self._drained


async def migrate(
    source: ConnectionSource,
    *,
    actor: Actor,
    schema: str,
    limits: StorageLimits | None = None,
    durability_profile: PostgresDurabilityProfile = NATIVE_POSTGRES_PROFILE,
) -> UUID:
    """Explicit owner-run migration; ordinary resource opening never migrates."""
    from tether.stores.postgres.schema import migrate as apply_migration

    return await apply_migration(
        source,
        actor=actor,
        schema=schema,
        limits=limits,
        durability_profile=durability_profile,
    )


__all__ = [
    "NATIVE_POSTGRES_PROFILE",
    "ConnectionSource",
    "PostgresDurabilityProfile",
    "PostgresResources",
    "RecoveredTurn",
    "StorageLimits",
    "migrate",
]
