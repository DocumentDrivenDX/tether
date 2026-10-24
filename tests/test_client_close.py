"""Session close seals pending groups atomically and retains unresolved authority."""

import asyncio

import pytest
from test_client_reads import harness as harness
from test_client_turns import entered
from test_client_turns import turns as turns

from tether.types.execution import Actor, Answer, EffectsUnresolved, ExecutionDenied, NewEntry
from tether.types.storage import SessionClosed, TurnInProgress, UnknownSession

pytestmark = pytest.mark.anyio


async def test_close_supersedes_pending_and_is_readable_idempotent(harness):
    h, sid = harness, harness.session.id
    pending = await h.client.pending(sid)
    before = await h.client.snapshot(sid)
    closed = await h.client.close(sid)
    assert closed.status == "closed"
    assert not closed.pending
    assert closed.turn_count == before.view.summary.turn_count
    assert not closed.resumable
    assert await h.client.pending(sid) is None
    assert await h.client.confirm(sid, pending.invocation_id, Answer(approved=True)) is None
    assert await h.client.close(sid) == closed
    assert not h.tool.calls
    assert len(h.model.requests) == 1
    entries = (await h.client.entries(sid)).items
    assert sum(e.key == "result:" + pending.invocation_id for e in entries) == 1
    assert any(
        e.key == "approval:" + pending.invocation_id and e.facts["state"] == "superseded"
        for e in entries
    )
    for operation in (
        lambda: h.client.update(sid, title="Denied"),
        lambda: h.client.send(sid, "Denied"),
    ):
        with pytest.raises(SessionClosed):
            await operation()


async def test_close_content_failure_has_no_accepted_closure_or_policy_fact(harness, monkeypatch):
    h, sid = harness, harness.session.id
    before = await h.client.snapshot(sid)

    async def fail(**kwargs):
        raise RuntimeError("fabricated_history_failure")

    monkeypatch.setattr(h.engine.history, "put", fail)
    with pytest.raises(RuntimeError, match="fabricated_history_failure"):
        await h.client.close(sid)
    assert await h.client.snapshot(sid) == before
    assert not h.tool.calls


async def test_close_refuses_unresolved_effect_and_preserves_resolution_authority(harness):
    h, sid = harness, harness.session.id
    async with h.store.turn(sid, h.actor.id, turn_id="fabricated-intent") as log:
        await log.append(
            [
                NewEntry(
                    kind="tool_call",
                    key="intent:unknown",
                    facts={
                        "invocation_id": "unknown",
                        "tool": "fabricated",
                        "status": "started",
                        "effect": "effectful",
                        "replay": "unsafe",
                    },
                )
            ]
        )
    before = await h.client.snapshot(sid)
    with pytest.raises(EffectsUnresolved):
        await h.client.close(sid)
    assert await h.client.snapshot(sid) == before
    assert len((await h.client.effects(sid)).items) == 1
    assert not h.tool.calls


async def test_close_is_owner_and_current_policy_authorized(harness):
    h, sid = harness, harness.session.id
    before = await h.client.snapshot(sid)
    with pytest.raises(UnknownSession):
        await h.app.client(Actor(id="other")).close(sid)
    h.policy.read = False
    with pytest.raises(ExecutionDenied):
        await h.client.close(sid)
    h.policy.read = True
    assert await h.client.snapshot(sid) == before


async def test_close_race_has_one_closure(harness):
    h, sid = harness, harness.session.id
    results = await asyncio.gather(h.client.close(sid), h.client.close(sid), return_exceptions=True)
    assert any(not isinstance(r, BaseException) and r.status == "closed" for r in results)
    assert all(not isinstance(r, BaseException) or isinstance(r, TurnInProgress) for r in results)
    assert (
        sum(
            e.kind == "session" and e.facts.get("event") == "closed"
            for e in (await h.client.entries(sid)).items
        )
        == 1
    )
    assert not h.tool.calls


async def test_close_refuses_active_task_without_canceling_it(turns):
    h = turns
    handle = await h.client.send(h.sid, "Fabricated")
    await entered(h.model)
    with pytest.raises(TurnInProgress):
        await h.client.close(h.sid)
    assert (await h.client.get(h.sid)).summary.status == "running"
    h.model.release.set()
    assert (await handle.result()).stop_reason == "final"
    assert (await h.client.close(h.sid)).status == "closed"


async def test_close_rechecks_policy_after_preparing_content(harness, monkeypatch):
    h, sid = harness, harness.session.id
    original = h.engine.history.put

    async def revoke(**kwargs):
        ref = await original(**kwargs)
        h.policy.read = False
        return ref

    monkeypatch.setattr(h.engine.history, "put", revoke)
    with pytest.raises(ExecutionDenied):
        await h.client.close(sid)
    h.policy.read = True
    view = await h.client.get(sid)
    assert view.summary.status == "waiting"
    assert view.summary.pending
    assert not h.tool.calls
    assert not any(
        e.kind == "session" and e.facts.get("event") == "closed"
        for e in (await h.client.entries(sid)).items
    )
