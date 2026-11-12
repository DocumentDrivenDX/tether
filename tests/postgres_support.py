"""Shared real PostgreSQL18 fixtures; all identifiers/content are fabricated."""

import pytest
from pgserver.postgres_server import get_server
from postgres_fixture_lifetime import database_context


@pytest.fixture(scope="session")
def postgres_server(tmp_path_factory):
    server = get_server(tmp_path_factory.mktemp("postgres18"), cleanup_mode="delete")
    yield server
    server.cleanup()


@pytest.fixture
async def database(postgres_server):
    async with database_context(postgres_server.get_uri()) as resources:
        yield resources
