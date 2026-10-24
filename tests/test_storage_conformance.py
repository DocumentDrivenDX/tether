"""The public testkit exercises actual ports without importing backend internals."""

from contextlib import asynccontextmanager
from typing import cast

import pytest
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server

from tether.stores.archive import InMemoryArchive
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.stores.memory import MemoryFiles
from tether.stores.postgres import DatabaseRoot, PostgresSessionStore
from tether.testing.conformance import (
    ArchiveFixture,
    ConformanceFailure,
    LiveStoreFixture,
    archive_suite,
    live_store_suite,
)
from tether.types.archive import ArchiveLimits
from tether.types.execution import Actor
from tether.types.storage import SessionStore, StoreCapabilities

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("backend", ["memory", "jsonl"])
async def test_public_live_store_suite(backend, tmp_path):
    @asynccontextmanager
    async def factory():
        root = await JsonlRoot.open(tmp_path) if backend == "jsonl" else MemoryFiles()
        try:
            store = await JsonlSessionStore.open(root)
            yield LiveStoreFixture(
                store, Actor(id="fabricated-owner"), Actor(id="fabricated-other")
            )
        finally:
            assert await root.aclose()

    report = await live_store_suite(factory)
    assert "journal_atomicity" in report.checks
    assert ("durable_reopen" in report.checks) == (backend == "jsonl")
    assert "adapter_faults" in report.required_external


async def test_public_postgres_suite_uses_caller_acquisition_and_borrowed_root(database):
    h = database

    @asynccontextmanager
    async def factory():
        root = await DatabaseRoot.open(h.source, actor=h.actor, schema=h.schema)
        try:
            yield LiveStoreFixture(
                PostgresSessionStore(root), h.actor, Actor(id="fabricated-other")
            )
        finally:
            assert await root.aclose()

    report = await live_store_suite(factory)
    assert "durable_reopen" in report.checks
    assert not h.pool.closed
    assert {actor.id for actor, _ in h.calls} >= {h.actor.id, "fabricated-other"}


async def test_public_archive_suite_preserves_original_content_and_owner_scope():
    @asynccontextmanager
    async def factory(sample):
        archive = InMemoryArchive(
            ArchiveLimits(max_sessions=10, max_entries=100, max_bytes=1048576)
        )
        yield ArchiveFixture(
            archive, Actor(id=sample.view.summary.owner), Actor(id="foreign"), archive
        )

    report = await archive_suite(factory)
    assert "archive_restore_capability" in report.checks
    assert "readiness_pending_denied_unavailable" in report.required_external


async def test_testkit_rejects_a_port_that_accepts_the_wrong_owner():
    class BrokenStore:
        capabilities = StoreCapabilities(durability="none", lease="process", fencing=True)

        def for_actor(self, actor):
            return self

        async def create(self, owner, init):
            return None

    @asynccontextmanager
    async def factory():
        yield LiveStoreFixture(
            cast(SessionStore, BrokenStore()), Actor(id="fabricated"), Actor(id="foreign")
        )

    with pytest.raises(ConformanceFailure, match="caller_owner_mismatch"):
        await live_store_suite(factory)
