"""Fresh public SQL Client resumes original accepted phases and resolves unsafe unknowns."""

from types import SimpleNamespace

import pytest
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from test_resume_engine import build, triggered

from tether import Tether
from tether.stores.postgres.capture import Capture
from tether.stores.postgres.store import PostgresSessionStore
from tether.types.storage import SessionInit

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def interrupt(h, run, stage, monkeypatch):
    session = await h.store.create(h.actor.id, SessionInit(settings={"approval": "auto"}))
    original = Capture.commit
    fired = False

    async def crash(store, token, values, **kwargs):
        nonlocal fired
        if not fired and triggered(stage, values):
            fired = True
            if stage != "result":
                await original(store, token, values, **kwargs)
            raise RuntimeError("fabricated_process_loss")
        return await original(store, token, values, **kwargs)

    with monkeypatch.context() as scope:
        scope.setattr(Capture, "commit", crash)
        with pytest.raises(RuntimeError, match="fabricated_process_loss"):
            async with h.store.turn(session.id, h.actor.id, turn_id="original") as log:
                await run.engine.run_turn(h.actor, log, "Fabricated input", spec=run.spec)
    assert fired
    view = await h.store.get(session.id, h.actor.id)
    assert view is not None
    assert view.summary.resumable
    return session.id


@pytest.mark.parametrize(
    ("stage", "safe", "effects"),
    [
        ("intent", False, 0),
        ("intent", True, 1),
        ("result", False, 1),
        ("result", True, 2),
    ],
)
async def test_sql_public_resume_and_manual_resolution_preserve_original_intents(
    database, limits, client_limits, monkeypatch, stage, safe, effects
):
    d = database
    factory = PostgresSessionStore(d.root, maintenance=d.actor)
    h = SimpleNamespace(
        store=factory.for_actor(d.actor),
        provider=d.history,
        actor=d.actor,
        limits=limits,
        engines=[],
    )
    run = build(h, safe=safe)
    sid = await interrupt(h, run, stage, monkeypatch)
    before = (await h.store.entries(sid, h.actor.id, start=0, limit=1000)).items
    assert await run.engine.aclose()
    fresh = build(h, safe=safe, tool=run.tool, model=run.model, spec=run.spec)
    async with Tether(
        engine=fresh.engine, store=factory, history=h.provider, limits=client_limits
    ) as app:
        client = app.client(h.actor)
        resumed = await client.resume(sid)
        assert (await resumed.result()).stop_reason == "final"
        assert len(run.tool.calls) == effects
        page = await client.entries(sid, start=0, limit=client_limits.max_page_size)
        assert page.next_start is None
        entries = page.items
        assert entries[: len(before)] == before
        intent = next(e for e in entries if e.key and e.key.startswith("intent:"))
        assert sum(e.key == intent.key for e in entries) == 1
        invocation = intent.facts["invocation_id"]
        assert isinstance(invocation, str)
        if safe:
            assert {inv for inv, _ in run.tool.calls} == {invocation}
            assert (await client.get(sid)).summary.unresolved_effect_count == 0
        else:
            assert (await client.get(sid)).summary.unresolved_effect_count == 1
            resolved = await client.resolve(sid, invocation, "completed", reason="operator_checked")
            assert resolved.unresolved_effect_count == 0
            assert (
                await client.resolve(sid, invocation, "completed", reason="operator_checked")
                == resolved
            )
        assert (await client.close(sid)).status == "closed"
    assert app.drained
