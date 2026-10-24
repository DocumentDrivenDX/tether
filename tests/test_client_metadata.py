"""Owner-scoped, bounded Client metadata commands and mutation serialization."""

import asyncio

import pytest
from test_client_reads import harness as harness
from test_client_turns import entered
from test_client_turns import turns as turns

from tether.types.client import ReadLimit
from tether.types.execution import Actor, ExecutionDenied, NewEntry
from tether.types.storage import SessionClosed, TurnInProgress, UnknownSession

pytestmark = pytest.mark.anyio


async def test_create_is_owner_scoped_and_ref_idempotent(harness):
    h = harness
    first, retry = await asyncio.gather(
        h.client.create(ref="new-reference", spec="demo", title="Fabricated", tags=["a"]),
        h.client.create(ref="new-reference", spec="demo", title="Fabricated", tags=["a"]),
    )
    assert first.id == retry.id
    assert first.owner == h.actor.id
    assert first.spec_id == "demo"
    other = await h.app.client(Actor(id="other")).create(ref="new-reference")
    assert other.id != first.id
    assert (await h.client.find("new-reference")).id == first.id
    assert (await h.client.get(first.id)).summary == first
    assert len(h.model.requests) == 1
    assert not h.tool.calls


async def test_update_preserves_omitted_title_and_explicit_none_clears_it(harness):
    h, sid = harness, harness.session.id
    before = await h.client.snapshot(sid)
    updated = await h.client.update(sid, tags=["updated"], settings={"fabricated": True})
    assert updated.title == "Fabricated"
    assert updated.tags == ("updated",)
    assert (await h.client.get(sid)).settings["fabricated"] is True
    assert (await h.client.update(sid, title=None)).title is None
    assert (await h.client.update(sid, title="Renamed")).title == "Renamed"
    snapshot = await h.client.snapshot(sid)
    assert snapshot.seq > before.seq
    assert snapshot.view.summary.turn_count == before.view.summary.turn_count
    assert snapshot.view.summary.pending
    feed = h.store.changes(h.actor.id, session_id=sid, since=before.seq)
    try:
        async with asyncio.timeout(5):
            changes = [await anext(feed) for _ in range(3)]
    finally:
        await feed.aclose()
    assert any(e.kind == "settings" for change in changes for e in change.entries)
    assert not h.tool.calls


async def test_metadata_policy_and_owner_refusals_leave_state_unchanged(harness):
    h, sid = harness, harness.session.id
    before = await h.client.snapshot(sid)
    with pytest.raises(UnknownSession):
        await h.app.client(Actor(id="other")).update(sid, title="Denied")
    h.policy.read = False
    with pytest.raises(ExecutionDenied):
        await h.client.update(sid, title="Denied")
    with pytest.raises(ExecutionDenied):
        await h.client.create(ref="denied")
    h.policy.read = True
    assert await h.client.find("denied") is None
    assert await h.client.snapshot(sid) == before


async def test_command_request_bounds_refuse_before_mutation(harness):
    h, sid = harness, harness.session.id
    oversized = "x" * (h.app.limits.max_request_bytes + 1)
    before = await h.client.snapshot(sid)
    with pytest.raises(ReadLimit):
        await h.client.update(sid, title=oversized)
    with pytest.raises(ReadLimit):
        await h.client.create(ref="oversized", settings={"large": oversized})
    assert await h.client.find("oversized") is None
    assert await h.client.snapshot(sid) == before


async def test_metadata_uses_current_disclosure_policy_after_known_commit(harness, monkeypatch):
    h, sid = harness, harness.session.id
    original = h.store.update

    async def revoke(*args, **kwargs):
        value = await original(*args, **kwargs)
        h.policy.read = False
        return value

    monkeypatch.setattr(h.store, "update", revoke)
    with pytest.raises(ExecutionDenied):
        await h.client.update(sid, title="Accepted")
    h.policy.read = True
    assert (await h.client.get(sid)).summary.title == "Accepted"


async def test_update_refuses_active_turn_then_succeeds_after_completion(turns):
    h = turns
    handle = await h.client.send(h.sid, "Fabricated")
    await entered(h.model)
    with pytest.raises(TurnInProgress):
        await h.client.update(h.sid, title="Busy")
    h.model.release.set()
    assert (await handle.result()).stop_reason == "final"
    assert (await h.client.update(h.sid, title="Available")).title == "Available"


async def test_update_refuses_closed_session(harness):
    h, sid = harness, harness.session.id
    async with h.store.turn(sid, h.actor.id, turn_id="closure-fixture") as log:
        await log.append([NewEntry(kind="session", facts={"event": "closed"})])
    before = await h.client.snapshot(sid)
    with pytest.raises(SessionClosed):
        await h.client.update(sid, title="Denied")
    assert await h.client.snapshot(sid) == before


@pytest.mark.parametrize("command", ["create", "update"])
async def test_metadata_cannot_replace_managed_held_context(harness, command):
    h, sid = harness, harness.session.id
    before = await h.client.snapshot(sid)
    watermark = await h.store.watermark(h.actor.id)
    settings = {"held": {"fabricated": ["id"]}}
    operation = (
        h.client.create(ref="managed", settings=settings)
        if command == "create"
        else h.client.update(sid, settings=settings)
    )
    with pytest.raises(ExecutionDenied, match="managed_setting"):
        await operation
    assert await h.client.find("managed") is None
    assert await h.client.snapshot(sid) == before
    assert await h.store.watermark(h.actor.id) == watermark


async def test_revocation_during_owner_lookup_prevents_update(harness, monkeypatch):
    h, sid = harness, harness.session.id
    before = await h.client.snapshot(sid)
    original = h.store.get

    async def revoke(*args, **kwargs):
        value = await original(*args, **kwargs)
        h.policy.read = False
        return value

    monkeypatch.setattr(h.store, "get", revoke)
    with pytest.raises(ExecutionDenied):
        await h.client.update(sid, title="Denied")
    h.policy.read = True
    assert await h.client.snapshot(sid) == before


async def test_metadata_copies_mutable_settings_before_policy_await(harness, monkeypatch):
    h, sid = harness, harness.session.id
    settings = {"fabricated": ["original"]}
    tags = ["original"]
    original = h.policy.authorize

    async def mutate(request):
        settings["fabricated"].append("changed")
        tags.append("changed")
        return await original(request)

    monkeypatch.setattr(h.policy, "authorize", mutate)
    await h.client.update(sid, tags=tags, settings=settings)
    monkeypatch.setattr(h.policy, "authorize", original)
    view = await h.client.get(sid)
    assert view.settings["fabricated"] == ["original"]
    assert view.summary.tags == ("original",)
