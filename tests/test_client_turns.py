"""Task ownership, canonical retries, and fenced heartbeat behavior through public Client."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from test_client_reads import CurrentPolicy
from test_client_reads import harness as harness
from test_gated_execution import final

from tether import Tether
from tether.engine import Engine
from tether.history.jsonl import JsonlHistory
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ScriptedModel
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.stores.memory import MemoryFiles
from tether.types import AttachmentRef
from tether.types.client import ReadLimit, RequestConflict
from tether.types.execution import Actor, AgentSpec, ExecutionDenied
from tether.types.history import HistoryValue
from tether.types.storage import LeaseLost, SessionInit, StoreError, TurnInProgress, UnknownSession

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class WaitingModel(ScriptedModel):
    def __init__(self):
        super().__init__([final(), final()])
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def stream(self, request):
        self.entered.set()
        await self.release.wait()
        async for item in super().stream(request):
            yield item


@pytest.fixture(params=["memory", "jsonl"])
async def turns(request, tmp_path, limits, client_limits):
    files = MemoryFiles() if request.param == "memory" else await JsonlRoot.open(tmp_path)
    store, history = await JsonlSessionStore.open(files), await JsonlHistory.open(files)
    actor, model, policy = Actor(id="owner"), WaitingModel(), CurrentPolicy()
    spec = AgentSpec(id="demo", version="1", title="Demo", instructions="Fabricated", model="demo")
    engine = Engine(
        model=model,
        history=MessageHistoryAdapter(history),
        history_provider=history,
        model_limits=limits,
        specs=(spec,),
        policy=policy,
    )
    session = await store.create(actor.id, SessionInit(spec="demo"))
    app = Tether(
        engine=engine,
        store=store,
        history=history,
        limits=client_limits.model_copy(update={"max_active_turns": 1}),
        resume="off" if request.param == "memory" else "report",
    )
    async with app:
        yield SimpleNamespace(
            app=app,
            client=app.client(actor),
            actor=actor,
            model=model,
            store=store,
            history=history,
            engine=engine,
            policy=policy,
            sid=session.id,
        )
    assert app.drained


async def entered(model):
    async with asyncio.timeout(5):
        await model.entered.wait()


async def test_send_returns_after_atomic_acceptance_and_duplicate_shares_one_task(turns):
    h = turns
    first = await h.client.send(h.sid, "Fabricated", request_id="same")
    await entered(h.model)
    snapshot = await h.client.snapshot(h.sid)
    assert snapshot.view.summary.status == "running"
    assert snapshot.view.summary.active_turn_id == first.turn_id
    accepted = await h.store.lookup(h.sid, h.actor.id, kind="turn", value=first.turn_id)
    assert any(e.key == "request:same" for e in accepted)
    assert any(e.history_ref is not None and e.facts.get("role") == "user" for e in accepted)
    second = await h.client.send(h.sid, "Fabricated", request_id="same")
    assert first.turn_id == second.turn_id
    with pytest.raises(TurnInProgress):
        await h.client.send(h.sid, "Other", request_id="new")
    h.model.release.set()
    assert await first.result() == await second.result()
    assert len(h.model.requests) == 1


@pytest.mark.parametrize("change", ["text", "attachment", "spec"])
async def test_same_request_key_binds_canonical_requested_input(turns, change):
    h = turns
    first = await h.client.send(h.sid, "Fabricated", request_id="same")
    requested_spec = None
    attachments = ()
    if change == "spec":
        requested_spec = "demo"
    if change == "attachment":
        attachments = (
            AttachmentRef(
                id="a", filename="a.txt", media_type="text/plain", size_bytes=1, read=False
            ),
        )
    with pytest.raises(RequestConflict):
        await h.client.send(
            h.sid,
            "Changed" if change == "text" else "Fabricated",
            request_id="same",
            spec=requested_spec,
            attachments=attachments,
        )
    h.model.release.set()
    assert (await first.result()).stop_reason == "final"
    assert len(h.model.requests) == 1


async def test_canceling_a_result_waiter_does_not_cancel_owned_turn(turns):
    h = turns
    handle = await h.client.send(h.sid, "Fabricated")
    await entered(h.model)
    waiting = asyncio.create_task(handle.result())
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert (await h.client.get(h.sid)).summary.status == "running"
    h.model.release.set()
    assert (await handle.result()).stop_reason == "final"


async def test_explicit_cancel_cancels_owned_task_and_pending_cancel_returns_false(turns):
    h = turns
    handle = await h.client.send(h.sid, "Fabricated")
    await entered(h.model)
    assert await h.client.cancel(h.sid)
    with pytest.raises(asyncio.CancelledError):
        await handle.result()
    assert not await h.client.cancel(h.sid)
    assert not h.engine._running
    assert (await h.client.get(h.sid)).summary.status == "interrupted"
    entries = await h.store.lookup(h.sid, h.actor.id, kind="turn", value=handle.turn_id)
    assert any(
        e.kind == "turn_ended" and e.facts.get("stop_reason") == "cancelled" for e in entries
    )


async def test_canceled_send_waiter_leaves_acceptance_owned_and_retryable(turns, monkeypatch):
    h = turns
    writing, release = asyncio.Event(), asyncio.Event()
    original = h.history.put

    async def blocked(**kwargs):
        writing.set()
        await release.wait()
        return await original(**kwargs)

    monkeypatch.setattr(h.history, "put", blocked)
    sending = asyncio.create_task(h.client.send(h.sid, "Fabricated", request_id="retry"))
    async with asyncio.timeout(5):
        await writing.wait()
    job = h.app.turns.jobs[h.sid]
    sending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sending
    assert not job.task.done()
    release.set()
    h.model.release.set()
    await asyncio.shield(job.task)
    retry = await h.client.send(h.sid, "Fabricated", request_id="retry")
    assert retry.turn_id == job.start.turn_id
    assert (await retry.result()).stop_reason == "final"
    assert len(h.model.requests) == 1


async def test_completed_retry_reconstructs_originals_without_an_engine_run(turns, monkeypatch):
    h = turns
    handle = await h.client.send(h.sid, "Fabricated", request_id="retry")
    h.model.release.set()
    expected = await handle.result()

    async def forbidden(*args, **kwargs):
        raise AssertionError("duplicate_agent_run")

    monkeypatch.setattr(h.engine, "run_turn", forbidden)
    retry = await h.client.send(h.sid, "Fabricated", request_id="retry")
    assert retry._task is None
    assert await retry.result() == expected
    assert len(h.model.requests) == 1

    async def unavailable(refs, *, actor):
        return tuple(HistoryValue(ref=ref, status="unavailable") for ref in refs)

    monkeypatch.setattr(h.history, "read", unavailable)
    with pytest.raises(ExecutionDenied, match="result_history_unavailable"):
        await retry.result()


async def test_request_limits_owner_isolation_and_active_task_capacity(turns, monkeypatch):
    h = turns
    with pytest.raises(ReadLimit, match="request_bytes"):
        await h.client.send(h.sid, "x" * (h.app.limits.max_request_bytes + 1))
    with pytest.raises(UnknownSession):
        await h.app.client(Actor(id="other")).send(h.sid, "Fabricated")
    handle = await h.client.send(h.sid, "Fabricated")
    other = await h.store.create(h.actor.id, SessionInit(spec="demo"))
    with pytest.raises(ReadLimit, match="active_turn_limit"):
        await h.client.send(other.id, "Fabricated")
    h.model.release.set()
    await handle.result()
    h.policy.read = False
    with pytest.raises(ExecutionDenied):
        await handle.result()
    with pytest.raises(ExecutionDenied):
        await h.client.send(h.sid, "Fabricated")
    h.policy.read = True


async def test_input_failure_is_not_accepted_and_can_retry(turns, monkeypatch):
    h = turns
    original = h.history.put

    async def failure(**kwargs):
        raise StoreError("fabricated_write_failure")

    monkeypatch.setattr(h.history, "put", failure)
    with pytest.raises(StoreError, match="fabricated_write_failure"):
        await h.client.send(h.sid, "Fabricated", request_id="retry")
    assert not await h.store.lookup(h.sid, h.actor.id, kind="key", value="request:retry")
    monkeypatch.setattr(h.history, "put", original)
    h.model.release.set()
    handle = await h.client.send(h.sid, "Fabricated", request_id="retry")
    assert (await handle.result()).stop_reason == "final"


async def test_owned_heartbeat_keeps_running_lease_current(turns, monkeypatch):
    h = turns
    renewed = asyncio.Event()
    original = h.store.heartbeat

    async def heartbeat(*args):
        await original(*args)
        renewed.set()

    monkeypatch.setattr(h.store, "heartbeat", heartbeat)
    handle = await h.client.send(h.sid, "Fabricated")
    async with asyncio.timeout(5):
        await renewed.wait()
    assert h.sid in h.app.turns.jobs
    h.model.release.set()
    assert (await handle.result()).stop_reason == "final"


async def test_heartbeat_failure_cancels_agent_and_propagates_lease_loss(turns, monkeypatch):
    h = turns

    async def lost(*args):
        await h.model.entered.wait()
        raise LeaseLost("lease_lost")

    monkeypatch.setattr(h.store, "heartbeat", lost)
    handle = await h.client.send(h.sid, "Fabricated")
    await entered(h.model)
    with pytest.raises(LeaseLost):
        await handle.result()
    assert not h.engine._running
    assert not h.app.turns.jobs
    assert not h.model.requests


@pytest.mark.parametrize("conflict", [False, True])
async def test_retry_rechecks_canonical_key_under_lease_after_stale_empty_lookup(
    turns, monkeypatch, conflict
):
    h = turns
    first = await h.client.send(h.sid, "Fabricated", request_id="race")
    h.model.release.set()
    expected = await first.result()
    before = await h.client.snapshot(h.sid)
    original, lookups = h.store.lookup, 0

    async def stale_once(*args, **kwargs):
        nonlocal lookups
        if kwargs.get("kind") == "key":
            lookups += 1
            if lookups == 1:
                return ()
        return await original(*args, **kwargs)

    monkeypatch.setattr(h.store, "lookup", stale_once)
    async with asyncio.timeout(5):
        if conflict:
            with pytest.raises(RequestConflict):
                await h.client.send(h.sid, "Changed", request_id="race")
        else:
            retry = await h.client.send(h.sid, "Fabricated", request_id="race")
            assert retry.turn_id == first.turn_id
            assert await retry.result() == expected
    assert lookups == 2
    assert (await h.client.snapshot(h.sid)).through_index == before.through_index
    assert len(h.model.requests) == 1


async def test_retained_handle_rechecks_current_source_permission(turns, monkeypatch):
    h = turns
    handle = await h.client.send(h.sid, "Fabricated", request_id="retained")
    h.model.release.set()
    assert (await handle.result()).messages

    async def denied(refs, *, actor):
        return tuple(HistoryValue(ref=ref, status="denied") for ref in refs)

    monkeypatch.setattr(h.history, "read", denied)
    with pytest.raises(ExecutionDenied, match="result_history_unavailable"):
        await handle.result()


async def test_same_owner_retry_uses_its_current_provider_credential(turns, monkeypatch):
    h = turns

    class SourceCredential:
        async def authorization(self):
            return None

    credential = SourceCredential()
    issuer = h.actor.model_copy(update={"credential": credential})
    original = h.history.read

    async def scoped(refs, *, actor):
        if actor.credential is not credential:
            return tuple(HistoryValue(ref=ref, status="denied") for ref in refs)
        return await original(refs, actor=actor)

    monkeypatch.setattr(h.history, "read", scoped)
    handle = await h.app.client(issuer).send(h.sid, "Fabricated", request_id="credential")
    h.model.release.set()
    assert (await handle.result()).messages
    retry = await h.client.send(h.sid, "Fabricated", request_id="credential")
    with pytest.raises(ExecutionDenied, match="result_history_unavailable"):
        await retry.result()
    assert (await handle.result()).messages
    assert len(h.model.requests) == 1


async def test_retained_waiting_result_does_not_revive_superseded_approval(harness):
    from test_gated_execution import proposal

    h = harness
    h.engine.model = ScriptedModel([proposal(), final()])
    handle = await h.client.send(h.session.id, "New fabricated approval")
    assert (await handle.result()).pending
    replacement = await h.client.send(h.session.id, "Superseding fabricated turn")
    assert (await replacement.result()).stop_reason == "final"
    old = await handle.result()
    assert old.stop_reason == "confirmation_required"
    assert not old.pending
    assert not h.tool.calls


async def test_one_read_deadline_bounds_post_completion_materialization(turns, monkeypatch):
    h = turns
    handle = await h.client.send(h.sid, "Fabricated")
    h.model.release.set()
    await handle.result()
    h.app.access.limits = h.app.access.limits.model_copy(update={"read_timeout_seconds": 0.02})

    async def blocked(refs, *, actor):
        await asyncio.Event().wait()

    monkeypatch.setattr(h.history, "read", blocked)
    with pytest.raises(TimeoutError):
        await handle.result()


async def test_shutdown_owns_and_cancels_send_before_acceptance(turns, monkeypatch):
    h = turns
    writing = asyncio.Event()

    async def blocked(**kwargs):
        writing.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(h.history, "put", blocked)
    sending = asyncio.create_task(h.client.send(h.sid, "Fabricated", request_id="closing"))
    async with asyncio.timeout(5):
        await writing.wait()
    assert await h.app.aclose()
    with pytest.raises(asyncio.CancelledError):
        await sending
    assert not h.app.turns.jobs
    assert not h.model.requests


async def test_shutdown_finishes_inflight_heartbeat_and_retains_root_ownership(turns, monkeypatch):
    from tether.types.storage import RootBusy

    h = turns
    entered_heartbeat, release = asyncio.Event(), asyncio.Event()
    original = h.store.heartbeat

    async def held(*args):
        entered_heartbeat.set()
        await release.wait()
        await original(*args)

    monkeypatch.setattr(h.store, "heartbeat", held)
    await h.client.send(h.sid, "Fabricated")
    async with asyncio.timeout(5):
        await entered_heartbeat.wait()
    h.model.release.set()
    async with asyncio.timeout(5):
        await h.engine._idle.wait()
    job = h.app.turns.jobs[h.sid]
    assert not job.task.done()
    h.app.limits = h.app.limits.model_copy(update={"shutdown_timeout_seconds": 0.02})
    try:
        async with asyncio.timeout(1):
            assert not await h.app.aclose()
        assert not h.store.files._closed
        if isinstance(h.store.files, JsonlRoot):
            with pytest.raises(RootBusy):
                await JsonlRoot.open(h.store.files.path)
    finally:
        release.set()
        if h.app._cleanup is not None:
            async with asyncio.timeout(5):
                await asyncio.shield(h.app._cleanup)
    assert h.app.drained
    assert not h.app.turns.jobs
    assert h.store.files._closed


async def test_old_handle_cannot_cancel_a_newer_owned_turn(turns):
    h = turns
    first = await h.client.send(h.sid, "First fabricated turn")
    h.model.release.set()
    await first.result()
    h.model.entered.clear()
    h.model.release.clear()
    second = await h.client.send(h.sid, "Second fabricated turn")
    await entered(h.model)
    await first.cancel()
    assert (await h.client.snapshot(h.sid)).view.summary.active_turn_id == second.turn_id
    h.model.release.set()
    assert (await second.result()).stop_reason == "final"
    assert len(h.model.requests) == 2


async def test_cancel_on_waiting_approval_does_not_decline_it(harness):
    h = harness
    before = await h.client.snapshot(h.session.id)
    assert before.view.pending is not None
    assert not await h.client.cancel(h.session.id)
    assert await h.client.snapshot(h.session.id) == before
    assert not h.tool.calls


async def test_setup_failure_after_acceptance_returns_handle_and_never_reexecutes(
    turns, monkeypatch
):
    h = turns

    def failed(*args):
        raise RuntimeError("fabricated_setup_failure")

    monkeypatch.setattr(h.engine, "_create", failed)
    handle = await h.client.send(h.sid, "Fabricated", request_id="accepted")
    with pytest.raises(RuntimeError, match="fabricated_setup_failure"):
        await handle.result()
    retry = await h.client.send(h.sid, "Fabricated", request_id="accepted")
    assert retry.turn_id == handle.turn_id
    assert (await retry.result()).stop_reason == "interrupted"
    assert not h.model.requests


async def test_request_retry_after_real_root_and_composition_restart(
    tmp_path, limits, client_limits
):
    spec = AgentSpec(id="demo", version="1", title="Demo", instructions="Fabricated", model="demo")
    actor = Actor(id="owner")
    root = await JsonlRoot.open(tmp_path)
    store, history = await JsonlSessionStore.open(root), await JsonlHistory.open(root)
    model = ScriptedModel([final()])
    engine = Engine(
        model=model,
        history=MessageHistoryAdapter(history),
        history_provider=history,
        model_limits=limits,
        specs=(spec,),
    )
    async with Tether(engine=engine, store=store, history=history, limits=client_limits) as first:
        session = await store.create(actor.id, SessionInit(spec="demo"))
        handle = await first.client(actor).send(session.id, "Fabricated", request_id="restart")
        expected = await handle.result()
    assert first.drained
    root = await JsonlRoot.open(tmp_path)
    store, history = await JsonlSessionStore.open(root), await JsonlHistory.open(root)
    model = ScriptedModel([])
    engine = Engine(
        model=model,
        history=MessageHistoryAdapter(history),
        history_provider=history,
        model_limits=limits,
    )
    async with Tether(engine=engine, store=store, history=history, limits=client_limits) as second:
        retry = await second.client(actor).send(session.id, "Fabricated", request_id="restart")
        assert retry.turn_id == handle.turn_id
        assert await retry.result() == expected
        assert not model.requests
        assert not second.turns.jobs
    assert second.drained


async def test_new_input_clears_interrupted_resumable_projection(turns):
    h = turns
    first = await h.client.send(h.sid, "Canceled fabricated input")
    await entered(h.model)
    await first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first.result()
    assert (await h.client.get(h.sid)).summary.resumable
    h.model.entered.clear()
    second = await h.client.send(h.sid, "New fabricated input")
    await entered(h.model)
    running = await h.client.get(h.sid)
    assert running.summary.status == "running"
    assert not running.summary.resumable
    h.model.release.set()
    assert (await second.result()).stop_reason == "final"
