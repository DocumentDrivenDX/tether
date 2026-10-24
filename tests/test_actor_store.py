"""Caller-bound storage keeps credential scopes explicit and resource ownership borrowed."""

import asyncio
from datetime import UTC, datetime

import pytest
from test_client_reads import harness as harness
from test_client_resume import FreshCredential
from test_resume_engine import recovery as recovery

from tether.ports.bound import ActorStore
from tether.types.execution import Actor, Decision, ExecutionDenied, SessionState, copy_actor
from tether.types.storage import (
    SessionArchive,
    SessionInit,
    SessionPatch,
    SessionQuery,
    Unsupported,
)

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "operation",
    [
        "get",
        "list",
        "find",
        "effects",
        "watermark",
        "changes",
        "lookup",
        "snapshot",
        "entries",
        "create",
        "update",
        "turn",
        "restore",
        "rebind",
    ],
)
async def test_bound_owner_mismatch_refuses_before_storage(recovery, operation):
    h = recovery
    scope = h.store.for_actor(h.actor)
    assert isinstance(scope, ActorStore)
    sid, owner = "missing", "other"
    with pytest.raises(ExecutionDenied, match="denied"):
        await foreign(scope, operation, sid, owner)
    assert not (await h.store.list(SessionQuery(owner=owner))).items


async def foreign(scope, operation, sid, owner):
    if operation == "list":
        await scope.list(SessionQuery(owner=owner))
    elif operation == "find":
        await scope.find(owner, "missing")
    elif operation == "watermark":
        await scope.watermark(owner)
    elif operation == "changes":
        scope.changes(owner)
    elif operation == "lookup":
        await scope.lookup(sid, owner, kind="key", value="missing")
    elif operation == "entries":
        await scope.entries(sid, owner, start=0, limit=1)
    elif operation in {"create", "update", "turn", "restore", "rebind"}:
        await foreign_write(scope, operation, sid, owner)
    else:
        await getattr(scope, operation)(sid, owner)


async def foreign_write(scope, operation, sid, owner):
    if operation == "create":
        await scope.create(owner, SessionInit())
    elif operation == "update":
        await scope.update(sid, owner, SessionPatch())
    elif operation == "turn":
        scope.turn(sid, owner, turn_id="missing")
    elif operation == "restore":
        await scope.restore(
            SessionArchive(state=SessionState(id=sid, owner=owner), init=SessionInit(), entries=()),
            created_at=datetime.now(UTC),
        )
    elif operation == "rebind":
        scope.for_actor(Actor(id=owner))


async def test_scope_close_does_not_close_root_or_sibling_and_cannot_recover_global(recovery):
    h = recovery
    first, second = h.store.for_actor(h.actor), h.store.for_actor(h.actor)
    session = await first.create(h.actor.id, SessionInit())
    assert first.workers is second.workers is h.store.workers
    with pytest.raises(Unsupported, match="maintenance_authority_required"):
        await first.recover(now=datetime.now(UTC))
    assert await first.aclose()
    assert await first.aclose()
    with pytest.raises(RuntimeError, match="store_scope_closed"):
        await first.get(session.id, h.actor.id)
    assert (await second.get(session.id, h.actor.id)).summary == session
    assert (await h.store.get(session.id, h.actor.id)).summary == session
    assert not h.store.workers.closed
    assert await h.store.recover(now=datetime.now(UTC)) == ()


async def test_same_owner_concurrent_clients_keep_distinct_live_actor_bindings(
    harness, monkeypatch
):
    h = harness
    first, second = FreshCredential(), FreshCredential()
    actors = [
        Actor(id=h.actor.id, principal="model-first", credential=first),
        Actor(id=h.actor.id, principal="model-second", credential=second),
    ]
    bindings = []
    original = h.store.for_actor

    def bind(actor):
        bindings.append(copy_actor(actor))
        return original(actor)

    monkeypatch.setattr(h.store, "for_actor", bind)
    clients = [h.app.client(actor) for actor in actors]
    values = await asyncio.gather(*(client.get(h.session.id) for client in clients))
    assert all(value.summary.owner == h.actor.id for value in values)
    assert [actor.credential for actor in bindings] == [first, second]
    assert [actor.principal for actor in bindings] == ["model-first", "model-second"]
    assert all(actor.id == h.actor.id for actor in bindings)
    assert all(actor is not original for actor, original in zip(bindings, actors, strict=True))


async def test_binding_provider_mutation_cannot_rewrite_final_policy_actor(harness, monkeypatch):
    h = harness
    original = h.store.for_actor
    revoked = False

    def bind(actor):
        nonlocal revoked
        actor.attributes["bypass"] = True
        revoked = True
        return original(actor)

    async def authorize(request):
        allowed = not revoked or request.actor.attributes.get("bypass") is True
        return Decision(allowed=allowed, reason="allowed" if allowed else "denied")

    monkeypatch.setattr(h.store, "for_actor", bind)
    monkeypatch.setattr(h.engine.policy, "authorize", authorize)
    with pytest.raises(ExecutionDenied):
        await h.client.get(h.session.id)
    assert revoked
    assert "bypass" not in h.actor.attributes


async def test_failed_caller_binding_never_falls_back_to_unbound_store(harness, monkeypatch):
    h = harness
    calls = []

    def deny(actor):
        raise ExecutionDenied("caller_acquisition_denied")

    async def unbound(*args, **kwargs):
        calls.append(True)
        raise AssertionError("unbound fallback")

    monkeypatch.setattr(h.store, "for_actor", deny)
    monkeypatch.setattr(h.store, "get", unbound)
    with pytest.raises(ExecutionDenied, match="caller_acquisition_denied"):
        await h.client.get(h.session.id)
    assert not calls


@pytest.mark.parametrize("operation", ["factory", "get", "close"])
async def test_bound_scope_wrong_loop_refuses_before_mutation(recovery, operation):
    h = recovery
    scope = h.store.for_actor(h.actor)
    session = await scope.create(h.actor.id, SessionInit())

    def foreign_loop():
        async def attempt():
            async def invoke():
                if operation == "factory":
                    h.store.for_actor(h.actor)
                elif operation == "close":
                    await scope.aclose()
                else:
                    await scope.get(session.id, h.actor.id)

            with pytest.raises(RuntimeError, match="wrong_event_loop"):
                await invoke()

        asyncio.run(attempt())

    await asyncio.to_thread(foreign_loop)
    value = await scope.get(session.id, h.actor.id)
    assert value is not None
    assert value.summary == session
