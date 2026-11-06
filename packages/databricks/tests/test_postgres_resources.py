"""Optional client PostgreSQL resources preserve caller ownership and actor scope."""

import pytest
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server

from tether.client import Actor, ExecutionDenied, SessionInit
from tether.client.postgres import PostgresResources, migrate

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_public_postgres_resources_borrow_pool_and_scope_store_operations(database):
    d = database
    identity = await migrate(d.source, actor=d.actor, schema=d.schema)
    resources = await PostgresResources.open(d.source, actor=d.actor, schema=d.schema)
    closed = False
    try:
        assert resources.storage.store.capabilities.durability == "os"
        alice = Actor(id="fabricated-alice")
        bob = Actor(id="fabricated-bob")
        alice_store = resources.storage.store.for_actor(alice)
        bob_store = resources.storage.store.for_actor(bob)
        alice_session = await alice_store.create(alice.id, SessionInit(title="Alice"))
        bob_session = await bob_store.create(bob.id, SessionInit(title="Bob"))

        assert await resources.recover_owner(alice) == ()
        alice_view = await alice_store.get(alice_session.id, alice.id)
        bob_view = await bob_store.get(bob_session.id, bob.id)
        assert alice_view is not None
        assert alice_view.summary.title == "Alice"
        assert bob_view is not None
        assert bob_view.summary.title == "Bob"
        with pytest.raises(ExecutionDenied, match="denied"):
            await alice_store.get(bob_session.id, bob.id)

        assert await resources.aclose()
        assert await resources.aclose()
        closed = True
        assert d.pool.closed is False
        async with d.pool.connection():
            pass
        assert await migrate(d.source, actor=d.actor, schema=d.schema) == identity
    finally:
        if not closed:
            await resources.aclose()
