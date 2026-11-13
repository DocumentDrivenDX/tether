"""Real optional PostgreSQL18 startup/persistence; all application actors are fabricated."""

import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import mkdtemp

import pytest
from embedded_lifetime_controls import close_embedded, close_matching, runtime_cleanup_safe
from postgres_pending_io_controls import PendingIOControl
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
def pending_io_control(monkeypatch):
    control = PendingIOControl()
    control.install(monkeypatch)
    return control


@pytest.fixture
async def config(pending_io_control):
    directory = Path(mkdtemp(prefix="te-", dir="/tmp"))
    data_dir, runtime_dir = directory / "data", directory / "runtime"
    runtime_dir.mkdir()
    configured = EmbeddedConfig(
        bootstrap_user="fabricated_admin",
        data_dir=data_dir,
        runtime_dir=runtime_dir,
        startup_seconds=20,
        shutdown_seconds=5,
        poll_seconds=0.02,
        max_roots=4,
    )
    primary = None
    try:
        yield configured
    except BaseException as error:
        primary = error
        raise
    finally:
        safe = False
        cleanup_error = None
        try:
            await close_matching(configured.data_dir)
            safe = not (configured.data_dir / "postmaster.pid").exists() and runtime_cleanup_safe(
                configured.data_dir
            )
        except BaseException as error:
            cleanup_error = error
        if safe:
            shutil.rmtree(directory)
        else:
            incomplete = AssertionError("embedded_runtime_preserved_with_native_owner")
            if cleanup_error is not None:
                incomplete = BaseExceptionGroup(
                    "embedded_runtime_cleanup_incomplete", [incomplete, cleanup_error]
                )
            if primary is not None:
                raise BaseExceptionGroup(
                    "embedded_test_and_runtime_cleanup_incomplete", [primary, incomplete]
                ) from primary
            raise incomplete


def acquisition(endpoint):
    @asynccontextmanager
    async def source(actor, session_id):
        async with await AsyncConnection.connect(**endpoint.model_dump()) as connection:
            yield connection

    return source


async def test_cold_and_warm_start_preserve_sql_session_without_implicit_migration(config):
    server = await EmbeddedPostgres.start(config)
    try:
        actor = Actor(id="fabricated-owner")
        source = acquisition(server.endpoint)
        await migrate(source, actor=actor, schema="fabricated")
        root = await server.open_root(source, actor=actor, schema="fabricated")
        store = PostgresSessionStore(root).for_actor(actor)
        session = await store.create(actor.id, SessionInit(ref="persistent"))
        async with store.turn(session.id, actor.id, turn_id="job") as log:
            await log.append([NewEntry(kind="message", key="original")])
    finally:
        await close_embedded(server, sys.exception())
    assert (config.data_dir / "PG_VERSION").is_file()
    reopened = await EmbeddedPostgres.start(config)
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
        await close_embedded(reopened, sys.exception())
