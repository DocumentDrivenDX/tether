"""Task-owned public resume uses durable local acceptance and current source results."""

import asyncio

import pytest
from test_client_turns import WaitingModel, entered
from test_gated_execution import MutablePolicy
from test_resume_engine import build, interrupted
from test_resume_engine import recovery as recovery

from tether import Tether
from tether.types.execution import Actor, Answer, ExecutionDenied, NotInterrupted
from tether.types.storage import TurnInProgress, Unsupported

pytestmark = pytest.mark.anyio


def app_for(h, engine, client_limits):
    return Tether(
        engine=engine,
        store=h.store,
        history=h.provider,
        limits=client_limits,
        resume="off" if h.store.capabilities.durability == "none" else "report",
    )


async def test_resume_requires_durable_fenced_store_and_materializes_chain(
    recovery, monkeypatch, client_limits
):
    h = recovery
    run = build(h)
    sid = await interrupted(h, run, "proposal", monkeypatch)
    fresh = build(h, tool=run.tool, model=run.model, spec=run.spec)
    async with app_for(h, fresh.engine, client_limits) as app:
        client = app.client(h.actor)
        if h.store.capabilities.durability == "none":
            with pytest.raises(Unsupported):
                await client.resume(sid)
            assert not run.tool.calls
            assert (await client.get(sid)).summary.turn_count == 1
        else:
            handle = await client.resume(sid)
            result = await handle.result()
            assert result.stop_reason == "final"
            assert result.messages[0].role == "user"
            assert result.messages[-1].role == "assistant"
            assert len(run.tool.calls) == 1
            assert (await client.get(sid)).summary.turn_count == 2
            with pytest.raises(NotInterrupted):
                await client.resume(sid)
    assert app.drained


@pytest.mark.parametrize("recovery", ["jsonl"], indirect=True)
async def test_resume_acceptance_precedes_blocked_model_and_waiter_cancel_isolated(
    recovery, monkeypatch, client_limits
):
    h = recovery
    run = build(h)
    sid = await interrupted(h, run, "input", monkeypatch)
    model = WaitingModel()
    fresh = build(h, tool=run.tool, model=model, spec=run.spec)
    async with app_for(h, fresh.engine, client_limits) as app:
        client = app.client(h.actor)
        handle = await client.resume(sid)
        await entered(model)
        summary = (await client.get(sid)).summary
        assert summary.status == "running"
        assert summary.active_turn_id == handle.turn_id
        assert not summary.resumable
        assert summary.turn_count == 2
        waiter = asyncio.create_task(handle.result())
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        with pytest.raises(TurnInProgress):
            await client.resume(sid)
        model.release.set()
        assert (await handle.result()).stop_reason == "final"
    assert app.drained


@pytest.mark.parametrize("recovery", ["jsonl"], indirect=True)
async def test_pending_rebind_is_visible_and_confirmable_through_public_client(
    recovery, monkeypatch, client_limits
):
    h = recovery
    run = build(h)
    sid = await interrupted(h, run, "pending", monkeypatch)
    fresh = build(h, tool=run.tool, model=run.model, spec=run.spec)
    async with app_for(h, fresh.engine, client_limits) as app:
        client = app.client(h.actor)
        old = await client.pending(sid)
        assert old is not None
        handle = await client.resume(sid)
        waiting = await handle.result()
        assert waiting.stop_reason == "confirmation_required"
        assert waiting.pending[0].invocation_id == old.invocation_id
        assert not run.tool.calls
        continued = await client.confirm(sid, old.invocation_id, Answer(approved=True))
        assert continued is not None
        assert continued.turn_id == handle.turn_id
        assert (await continued.result()).stop_reason == "final"
        assert len(run.tool.calls) == 1
        assert (await client.get(sid)).summary.turn_count == 2
    assert app.drained


@pytest.mark.parametrize("recovery", ["jsonl"], indirect=True)
async def test_resume_policy_denial_does_not_accept_a_new_turn(
    recovery, monkeypatch, client_limits
):
    h = recovery
    run = build(h)
    sid = await interrupted(h, run, "proposal", monkeypatch)
    fresh = build(h, tool=run.tool, model=run.model, spec=run.spec)
    policy = MutablePolicy()
    policy.deny.add("resume")
    fresh.engine.policy = policy
    async with app_for(h, fresh.engine, client_limits) as app:
        client = app.client(h.actor)
        with pytest.raises(ExecutionDenied):
            await client.resume(sid)
        summary = (await client.get(sid)).summary
        assert summary.resumable
        assert summary.turn_count == 1
        assert not run.tool.calls
        assert len(run.model.requests) == 1
    assert app.drained


class FreshCredential:
    async def authorization(self) -> str:
        return "fabricated" + "_credential"


@pytest.mark.parametrize("recovery", ["jsonl"], indirect=True)
async def test_resume_uses_present_actor_principal_and_credential(
    recovery, monkeypatch, client_limits
):
    h = recovery
    run = build(h)
    sid = await interrupted(h, run, "input", monkeypatch)
    fresh = build(h, tool=run.tool, model=run.model, spec=run.spec)
    credential = FreshCredential()
    actor = Actor(id=h.actor.id, principal="current-principal", credential=credential)
    original, observed = run.model.stream, []

    async def capture(request):
        observed.append((request.tags.principal, request.credential is credential))
        async for item in original(request):
            yield item

    monkeypatch.setattr(run.model, "stream", capture)
    async with app_for(h, fresh.engine, client_limits) as app:
        handle = await app.client(actor).resume(sid)
        assert (await handle.result()).stop_reason == "final"
        assert all(r.tags.principal == actor.principal for r in run.model.requests)
        assert observed == [(actor.principal, True)] * len(run.model.requests)
        assert all(r.credential is None for r in run.model.requests)
    assert app.drained


@pytest.mark.parametrize("recovery", ["jsonl"], indirect=True)
async def test_resume_after_actual_root_and_engine_reopen_keeps_original_invocation(
    recovery,
    monkeypatch,
    client_limits,
    tmp_path,
):
    from tether.history.jsonl import JsonlHistory
    from tether.stores.jsonl import JsonlRoot, JsonlSessionStore

    h = recovery
    run = build(h, safe=True)
    sid = await interrupted(h, run, "result", monkeypatch)
    invocation = run.tool.calls[0][0]
    assert await run.engine.aclose()
    assert await h.store.aclose()
    root = await JsonlRoot.open(tmp_path)
    h.store = await JsonlSessionStore.open(root)
    h.provider = await JsonlHistory.open(root)
    fresh = build(h, safe=True, tool=run.tool, model=run.model, spec=run.spec)
    async with app_for(h, fresh.engine, client_limits) as app:
        client = app.client(h.actor)
        handle = await client.resume(sid)
        result = await handle.result()
        assert result.stop_reason == "final"
        assert {inv for inv, _ in run.tool.calls} == {invocation}
        assert len(run.tool.calls) == 2
        assert (await client.get(sid)).summary.unresolved_effect_count == 0
    assert app.drained


@pytest.mark.parametrize("recovery", ["jsonl"], indirect=True)
async def test_resume_cannot_use_denied_originals_as_empty_history(
    recovery, monkeypatch, client_limits
):
    from tether.types.history import HistoryValue

    h = recovery
    run = build(h)
    sid = await interrupted(h, run, "proposal", monkeypatch)
    fresh = build(h, tool=run.tool, model=run.model, spec=run.spec)

    async def denied(refs, *, actor):
        return tuple(HistoryValue(ref=ref, status="denied") for ref in refs)

    monkeypatch.setattr(h.provider, "read", denied)
    async with app_for(h, fresh.engine, client_limits) as app:
        client = app.client(h.actor)
        with pytest.raises(ExecutionDenied):
            await client.resume(sid)
        summary = (await client.get(sid)).summary
        assert summary.turn_count == 1
        assert summary.resumable
        assert not run.tool.calls
        assert len(run.model.requests) == 1
    assert app.drained
