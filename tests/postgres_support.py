"""Shared real PostgreSQL18 fixtures; all identifiers/content are fabricated."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pgserver.postgres_server import get_server
from psycopg import sql
from psycopg_pool import AsyncConnectionPool

from tether.stores.postgres import DatabaseRoot, PostgresHistory, migrate
from tether.types.execution import Actor


@pytest.fixture(scope="session")
def postgres_server(tmp_path_factory):
    server = get_server(tmp_path_factory.mktemp("postgres18"), cleanup_mode="delete")
    yield server
    server.cleanup()


@pytest.fixture
async def database(postgres_server):
    pool = AsyncConnectionPool(
        postgres_server.get_uri(),
        min_size=0,
        max_size=8,
        open=False,
        timeout=3,
        kwargs={"connect_timeout": 3},
    )
    await pool.open()
    schema = "test_" + uuid4().hex
    actor = Actor(id="fabricated-owner")
    calls = []

    @asynccontextmanager
    async def source(caller, session_id):
        calls.append((caller, session_id))
        async with pool.connection() as connection:
            yield connection

    await migrate(source, actor=actor, schema=schema)
    root = await DatabaseRoot.open(source, actor=actor, schema=schema)
    try:
        yield SimpleNamespace(
            pool=pool,
            source=source,
            root=root,
            schema=schema,
            actor=actor,
            calls=calls,
            history=PostgresHistory(root),
        )
    finally:
        assert await root.aclose()
        async with pool.connection() as connection:
            await connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )
        await pool.close()
