"""Real optional PostgreSQL18 startup/persistence; all application actors are fabricated."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import pytest
from psycopg import AsyncConnection

from tether.stores.postgres import PostgresSessionStore, migrate
from tether.stores.postgres.embedded import EmbeddedConfig, EmbeddedPostgres
from tether.types.execution import Actor, NewEntry
from tether.types.storage import SessionInit

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def config(tmp_path):
    with TemporaryDirectory(prefix="te-", dir="/tmp") as directory:
        configured = EmbeddedConfig(
            bootstrap_user="fabricated_admin",
            data_dir=tmp_path / "data",
            runtime_dir=Path(directory),
            startup_seconds=20,
            shutdown_seconds=5,
            poll_seconds=0.02,
            max_roots=4,
        )
        try:
            yield configured
        finally:
            from tether.stores.postgres.embedded.server import _LIVE

            for owner in tuple(_LIVE):
                if owner.config.data_dir.resolve() == configured.data_dir.resolve():
                    owner._seal()
                    await asyncio.wait_for(asyncio.shield(owner._ensure_close()), 20)


def acquisition(endpoint):
    @asynccontextmanager
    async def source(actor, session_id):
        async with await AsyncConnection.connect(**endpoint.model_dump()) as connection:
            yield connection

    return source


async def test_cold_and_warm_start_preserve_sql_session_without_implicit_migration(config):
    actor = Actor(id="fabricated-owner")
    started = perf_counter()
    server = await EmbeddedPostgres.start(config)
    cold_seconds = perf_counter() - started
    source = acquisition(server.endpoint)
    try:
        await migrate(source, actor=actor, schema="fabricated")
        root = await server.open_root(source, actor=actor, schema="fabricated")
        store = PostgresSessionStore(root).for_actor(actor)
        session = await store.create(actor.id, SessionInit(ref="persistent"))
        async with store.turn(session.id, actor.id, turn_id="job") as log:
            await log.append([NewEntry(kind="message", key="original")])
    finally:
        assert await server.aclose()
    assert (config.data_dir / "PG_VERSION").is_file()
    started = perf_counter()
    reopened = await EmbeddedPostgres.start(config)
    warm_seconds = perf_counter() - started
    try:
        source = acquisition(reopened.endpoint)
        root = await reopened.open_root(source, actor=actor, schema="fabricated")
        store = PostgresSessionStore(root).for_actor(actor)
        restored = await store.find(actor.id, "persistent")
        assert restored is not None
        assert restored.id == session.id
        assert (await store.entries(session.id, actor.id, start=0, limit=10)).items[
            0
        ].key == "original"
    finally:
        assert await reopened.aclose()
    print({"cold_seconds": cold_seconds, "warm_seconds": warm_seconds})
