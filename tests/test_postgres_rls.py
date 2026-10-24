"""Application-owned role/GUC setup composes with caller predicates and live credentials."""

from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from psycopg import sql

from tether.stores.postgres import DatabaseRoot
from tether.stores.postgres.store import PostgresSessionStore
from tether.types.execution import Actor, ExecutionDenied
from tether.types.storage import SessionInit, SessionPatch, SessionQuery

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FreshCredential:
    def __init__(self):
        self.calls = 0

    async def authorization(self):
        self.calls += 1
        return "fabricated" + str(self.calls)


async def configure(h, role):
    async with h.pool.connection() as connection:
        await connection.execute(
            sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role))
        )
        await connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(h.schema), sql.Identifier(role)
            )
        )
        await connection.execute(
            sql.SQL("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {} TO {}").format(
                sql.Identifier(h.schema), sql.Identifier(role)
            )
        )
        tables = (
            "owner_heads",
            "sessions",
            "entries",
            "entry_groups",
            "sources",
            "pending",
            "effects",
            "attempt_usage",
            "changes",
            "history",
        )
        for name in tables:
            table = sql.Identifier(h.schema, name)
            await connection.execute(
                sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(table)
            )
            await connection.execute(
                sql.SQL(
                    "CREATE POLICY caller_owner ON {} USING "
                    "(owner=convert_to(current_setting('fabricated.owner',true),'UTF8')) "
                    "WITH CHECK (owner=convert_to(current_setting('fabricated.owner',true),'UTF8'))"
                ).format(table)
            )


class Acquisition:
    def __init__(self, h, role):
        self.h, self.role = h, role
        self.denied = False
        self.seen = []

    @asynccontextmanager
    async def __call__(self, actor, sid):
        self.seen.append((actor.id, actor.credential, sid))
        if self.denied and actor.id != "maintenance":
            raise ExecutionDenied("fabricated_expired_credential")
        if actor.credential is not None:
            await actor.credential.authorization()
        async with self.h.pool.connection() as connection:
            await connection.set_autocommit(True)
            try:
                if actor.id != "maintenance":
                    await connection.execute(
                        sql.SQL("SET ROLE {}").format(sql.Identifier(self.role))
                    )
                    await connection.execute(
                        "SELECT set_config('fabricated.owner',%s,false)", (actor.id,)
                    )
                yield connection
            finally:
                await connection.execute("RESET ROLE")
                await connection.execute("SELECT set_config('fabricated.owner','',false)")
                await connection.set_autocommit(False)


async def test_application_rls_role_and_credential_refresh_without_authority_fallback(database):
    h = database
    role = "fabricated_" + uuid4().hex
    await configure(h, role)
    source = Acquisition(h, role)
    maintenance = Actor(id="maintenance", credential=FreshCredential())
    root = await DatabaseRoot.open(source, actor=maintenance, schema=h.schema)
    factory = PostgresSessionStore(root, maintenance=maintenance)
    credentials = [FreshCredential(), FreshCredential()]
    actors = [Actor(id=str(i), principal="same-model", credential=credentials[i]) for i in range(2)]
    try:
        scopes = [factory.for_actor(actor) for actor in actors]
        sessions = [
            await store.create(actor.id, SessionInit(ref="same"))
            for store, actor in zip(scopes, actors, strict=True)
        ]
        for store, actor, session in zip(scopes, actors, sessions, strict=True):
            assert (await store.list(SessionQuery(owner=actor.id))).items[0].id == session.id
            updated = await store.update(session.id, actor.id, SessionPatch(title="Changed"))
            assert updated.owner == actor.id
        assert await scopes[0].get(sessions[1].id, actors[0].id) is None
        assert all(credential.calls > 1 for credential in credentials)
        source.seen.clear()
        source.denied = True
        with pytest.raises(ExecutionDenied, match="fabricated_expired_credential"):
            await scopes[0].create(actors[0].id, SessionInit())
        assert [owner for owner, _, _ in source.seen] == [actors[0].id]
        assert source.seen[0][1] is credentials[0]
        assert not h.pool.closed
    finally:
        assert await root.aclose()
        async with h.pool.connection() as connection:
            await connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
