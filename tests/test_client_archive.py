"""Present-caller archive browsing, reference hydration and explicit restore."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from test_archive_ports import archive, snapshot
from test_client_reads import CurrentPolicy
from test_resume_engine import build
from test_resume_engine import recovery as recovery

from tether import Tether
from tether.history.jsonl import JsonlHistory
from tether.stores.jsonl import JsonlSessionStore
from tether.stores.memory import MemoryFiles
from tether.types.archive import ArchiveCapabilities, ArchiveReadError, NotRestorable
from tether.types.execution import Answer, ExecutionDenied
from tether.types.storage import SessionInit, StoreError, UnknownSession

pytestmark = pytest.mark.anyio


async def archived(h, *, manual=False):
    run = build(h)
    session = await h.store.create(
        h.actor.id, SessionInit(settings={"approval": "manual" if manual else "auto"})
    )
    async with h.store.turn(session.id, h.actor.id, turn_id="original") as log:
        await run.engine.run_turn(h.actor, log, "Fabricated", spec=run.spec)
    sink = archive()
    await sink.archive(await snapshot(h, session.id))
    return run, session.id, sink


@asynccontextmanager
async def target(h, run, sink, client_limits):
    files = MemoryFiles()
    store = await JsonlSessionStore.open(files)
    provider = await JsonlHistory.open(files)
    target_h = SimpleNamespace(provider=provider, limits=h.limits, engines=h.engines)
    engine = build(target_h, tool=run.tool, spec=run.spec, model=run.model).engine
    policy = CurrentPolicy()
    engine.policy = policy
    async with Tether(
        engine=engine,
        store=store,
        history=provider,
        limits=client_limits,
        resume="off",
        archive=sink,
    ) as app:
        yield app.client(h.actor), policy, store


async def test_client_browses_archive_then_restores_pending_with_fresh_gates(
    recovery, client_limits
):
    h = recovery
    run, sid, sink = await archived(h, manual=True)
    before = len(run.model.requests)
    async with target(h, run, sink, client_limits) as (client, policy, store):
        page = await client.list(tier="archive")
        assert [v.id for v in page.items] == [sid]
        assert not (await client.list()).items
        assert (await client.get(sid)).summary.tier == "archive"
        with pytest.raises(UnknownSession):
            await client.get(sid, tier="live")
        assert (await client.entries(sid)).items
        history = await client.history(sid, tier="archive")
        assert history.items
        assert all(slot.value.status == "ready" for slot in history.items)
        assert len(run.model.requests) == before
        assert not run.tool.calls
        restored = await client.restore(sid)
        assert restored.tier == "live"
        assert restored.status == "waiting"
        assert await client.restore(sid) == restored
        assert (await client.get(sid)).summary.tier == "live"
        pending = await client.pending(sid)
        assert pending is not None
        policy.read = False
        with pytest.raises(ExecutionDenied):
            await client.confirm(sid, pending.invocation_id, Answer(approved=True))
        assert not run.tool.calls
        policy.read = True
        handle = await client.confirm(sid, pending.invocation_id, Answer(approved=True))
        assert handle is not None
        assert (await handle.result()).stop_reason == "final"
        assert len(run.tool.calls) == 1
        current = await store.get(sid, h.actor.id)
        assert current is not None
        assert current.summary.turn_count == 1


@pytest.mark.parametrize("status", ["pending", "denied", "unavailable"])
async def test_archive_status_is_not_empty_or_a_fallback(
    recovery, client_limits, monkeypatch, status
):
    h = recovery
    run, sid, sink = await archived(h)

    async def status_get(session_id, *, actor):
        raise ArchiveReadError(status)

    monkeypatch.setattr(sink, "get", status_get)
    async with target(h, run, sink, client_limits) as (client, _policy, _store):
        with pytest.raises(ArchiveReadError) as error:
            await client.get(sid)
        assert error.value.status == status


async def test_revocation_after_archive_io_refuses_disclosure(recovery, client_limits, monkeypatch):
    h = recovery
    run, sid, sink = await archived(h)
    original = sink.get
    async with target(h, run, sink, client_limits) as (client, policy, _store):
        calls = []

        async def revoked(session_id, *, actor):
            calls.append(actor.id)
            value = await original(session_id, actor=actor)
            policy.read = False
            return value

        monkeypatch.setattr(sink, "get", revoked)
        with pytest.raises(ExecutionDenied):
            await client.get(sid, tier="archive")
        assert calls == [h.actor.id]
        with pytest.raises(ExecutionDenied):
            await client.get(sid)
        assert calls == [h.actor.id]


async def test_derived_archive_is_partial_and_not_restorable(recovery, client_limits):
    h = recovery
    run, sid, sink = await archived(h)
    sink.capabilities = ArchiveCapabilities(
        kind="derived", writable=False, lossless=False, restorable=False, content="facts_only"
    )
    async with target(h, run, sink, client_limits) as (client, _policy, store):
        assert (await client.get(sid, tier="archive")).summary.partial
        assert (await client.entries(sid, tier="archive")).partial
        assert (await client.history(sid, tier="archive")).partial
        assert all(v.partial for v in (await client.list(tier="archive")).items)
        with pytest.raises(NotRestorable):
            await client.restore(sid)
        assert await store.get(sid, h.actor.id) is None


async def test_explicit_archive_helper_is_outside_turn_and_preserves_live_data(
    recovery, client_limits
):
    h = recovery
    run = build(h)
    session = await h.store.create(h.actor.id, SessionInit())
    sink = archive()
    async with Tether(
        engine=run.engine,
        store=h.store,
        history=h.provider,
        limits=client_limits,
        resume="off",
        archive=sink,
        archive_sink=sink,
    ) as app:
        client = app.client(h.actor)
        handle = await client.send(session.id, "Fabricated", spec="demo")
        await handle.result()
        calls = len(run.tool.calls), len(run.model.requests)
        await app.archive_session(h.actor, session.id)
        await app.archive_session(h.actor, session.id)
        assert (await client.get(session.id, tier="live")).summary.tier == "live"
        assert (await client.get(session.id, tier="archive")).summary.tier == "archive"
        assert calls == (len(run.tool.calls), len(run.model.requests))


async def test_waiting_archive_helper_refuses_without_changing_turn(recovery, client_limits):
    h = recovery
    run, sid, sink = await archived(h, manual=True)
    async with Tether(
        engine=run.engine,
        store=h.store,
        history=h.provider,
        limits=client_limits,
        resume="off",
        archive_sink=sink,
    ) as app:
        before = await h.store.snapshot(sid, h.actor.id)
        with pytest.raises(StoreError, match="archive_session_running"):
            await app.archive_session(h.actor, sid)
        assert await h.store.snapshot(sid, h.actor.id) == before
        assert not run.tool.calls


async def test_archive_history_rechecks_payload_after_last_metadata_await(
    recovery, client_limits, monkeypatch
):
    from tether.types.archive_codec import references

    h = recovery
    run, sid, sink = await archived(h)
    value = await sink.restore(sid, actor=h.actor)
    expected = sum(
        len(list(references(e.model_dump(mode="json")))) for e in value.bundle.session.entries
    )
    async with target(h, run, sink, client_limits) as (client, policy, _store):
        reads, checks = 0, 0
        original_read, original_authorize = sink.read, policy.authorize

        async def read(refs, *, actor):
            nonlocal reads
            result = await original_read(refs, actor=actor)
            reads += len(refs)
            return result

        async def authorize(request):
            nonlocal checks
            if reads == expected and request.action == "read_session":
                checks += 1
                if checks == 2:
                    policy.payload = False
            return await original_authorize(request)

        monkeypatch.setattr(sink, "read", read)
        monkeypatch.setattr(policy, "authorize", authorize)
        page = await client.history(sid, tier="archive")
        raw = [slot.value for slot in page.items if slot.value.ref.kind == "model_exchange"]
        assert raw
        assert checks == 2
        assert all(v.status == "denied" and v.record is None for v in raw)
        assert any(slot.value.status == "ready" for slot in page.items)


async def test_restore_payload_denial_precedes_bundle_fetch(recovery, client_limits, monkeypatch):
    h = recovery
    run, sid, sink = await archived(h)
    calls = []
    original = sink.restore

    async def restore(session_id, *, actor):
        calls.append(session_id)
        return await original(session_id, actor=actor)

    monkeypatch.setattr(sink, "restore", restore)
    async with target(h, run, sink, client_limits) as (client, policy, store):
        policy.payload = False
        with pytest.raises(ExecutionDenied):
            await client.restore(sid)
        assert not calls
        assert await store.get(sid, h.actor.id) is None


async def test_restore_revocation_after_content_keeps_journal_absent_and_retry_safe(
    recovery, client_limits, monkeypatch
):
    h = recovery
    run, sid, sink = await archived(h)
    value = await sink.restore(sid, actor=h.actor)
    original = JsonlHistory.put
    async with target(h, run, sink, client_limits) as (client, policy, store):
        writes = 0

        async def put(provider, **kwargs):
            nonlocal writes
            ref = await original(provider, **kwargs)
            if provider.files is store.files:
                writes += 1
                if writes == len(value.bundle.records):
                    policy.payload = False
            return ref

        monkeypatch.setattr(JsonlHistory, "put", put)
        with pytest.raises(ExecutionDenied):
            await client.restore(sid)
        assert writes == len(value.bundle.records)
        assert await store.get(sid, h.actor.id) is None
        policy.payload = True
        assert (await client.restore(sid)).tier == "live"
        assert len(run.tool.calls) == 1


async def test_client_cursor_scope_refuses_before_archive_adapter_lookup(
    recovery, client_limits, monkeypatch
):
    from tether.types.storage import SessionQuery

    h = recovery
    run, _sid, sink = await archived(h)
    other = await h.store.create(h.actor.id, SessionInit())
    await sink.archive(await snapshot(h, other.id))
    calls = []
    original = sink.list

    async def listing(*args, **kwargs):
        calls.append(True)
        return await original(*args, **kwargs)

    monkeypatch.setattr(sink, "list", listing)
    async with target(h, run, sink, client_limits) as (client, _policy, _store):
        first = await client.list(tier="archive", limit=1)
        assert first.next
        assert len(calls) == 1
        assert (await client.list(tier="archive", limit=1, cursor=first.next)).items
        assert len(calls) == 2
        with pytest.raises(ValueError, match="invalid_cursor"):
            await client.list(
                SessionQuery(owner=h.actor.id, tags_all=("other",)),
                tier="archive",
                cursor=first.next,
            )
        with pytest.raises(ValueError, match="invalid_cursor"):
            await client.list(tier="archive", cursor="live-cursor")
        assert len(calls) == 2


async def test_restore_provider_cannot_rewrite_later_authorization_actor(
    recovery, client_limits, monkeypatch
):
    from tether.types.execution import Decision

    h = recovery
    run, sid, sink = await archived(h)
    original = JsonlHistory.put
    async with target(h, run, sink, client_limits) as (client, policy, store):
        revoked = False

        async def authorize(request):
            allowed = not revoked or request.actor.attributes.get("bypass") is True
            return Decision(allowed=allowed, reason="allowed" if allowed else "denied")

        async def put(provider, **kwargs):
            nonlocal revoked
            result = await original(provider, **kwargs)
            kwargs["actor"].attributes["bypass"] = True
            revoked = True
            return result

        monkeypatch.setattr(policy, "authorize", authorize)
        monkeypatch.setattr(JsonlHistory, "put", put)
        with pytest.raises(ExecutionDenied):
            await client.restore(sid)
        assert revoked
        assert "bypass" not in h.actor.attributes
        assert await store.get(sid, h.actor.id) is None


@pytest.mark.parametrize("recovery", ["jsonl"], indirect=True)
async def test_unknown_restore_ack_reopens_and_retries_same_archive(
    recovery, client_limits, tmp_path, monkeypatch
):
    from tether.stores.jsonl import JsonlRoot
    from tether.types.execution import CommitUnknown

    h = recovery
    run, sid, sink = await archived(h)
    path = tmp_path / "target"
    root = await JsonlRoot.open(path)
    store = await JsonlSessionStore.open(root)
    provider = await JsonlHistory.open(root)
    fresh_h = SimpleNamespace(provider=provider, limits=h.limits, engines=h.engines)
    engine = build(fresh_h, tool=run.tool, spec=run.spec).engine
    append = root.append_file

    async def uncertain(name, value, **kwargs):
        result = await append(name, value, **kwargs)
        if name == "journal" and isinstance(value, dict) and value.get("operation") == "restore":
            raise CommitUnknown("fabricated_restore_ack_loss")
        return result

    monkeypatch.setattr(root, "append_file", uncertain)
    async with Tether(
        engine=engine, store=store, history=provider, limits=client_limits, archive=sink
    ) as app:
        with pytest.raises(CommitUnknown):
            await app.client(h.actor).restore(sid)
    root = await JsonlRoot.open(path)
    store = await JsonlSessionStore.open(root)
    provider = await JsonlHistory.open(root)
    fresh_h.provider = provider
    engine = build(fresh_h, tool=run.tool, spec=run.spec).engine
    async with Tether(
        engine=engine, store=store, history=provider, limits=client_limits, archive=sink
    ) as app:
        client = app.client(h.actor)
        before = await client.snapshot(sid)
        assert await client.restore(sid) == before.view.summary
        assert await client.snapshot(sid) == before
        assert not engine.model.requests
        assert len(run.tool.calls) == 1


async def test_archive_timeout_is_unavailable_and_byte_limit_precedes_hydration(
    recovery, client_limits, monkeypatch
):
    import asyncio

    from tether.types.client import ReadLimit

    h = recovery
    run, sid, sink = await archived(h)
    async with target(
        h, run, sink, client_limits.model_copy(update={"read_timeout_seconds": 0.01})
    ) as (client, _policy, _store):
        original = sink.get

        async def slow(session_id, *, actor):
            await asyncio.Event().wait()

        monkeypatch.setattr(sink, "get", slow)
        with pytest.raises(ArchiveReadError) as error:
            await client.get(sid, tier="archive")
        assert error.value.status == "unavailable"
        monkeypatch.setattr(sink, "get", original)
    async with target(h, run, sink, client_limits.model_copy(update={"max_read_bytes": 100})) as (
        client,
        _policy,
        _store,
    ):
        calls = []

        async def read(*args, **kwargs):
            calls.append(True)
            raise AssertionError("oversized page reached payload hydration")

        monkeypatch.setattr(sink, "read", read)
        with pytest.raises(ReadLimit, match="read_bytes"):
            await client.history(sid, tier="archive")
        assert not calls


async def test_sink_failure_does_not_change_successful_turn(recovery, client_limits, monkeypatch):
    h = recovery
    run = build(h)
    sink = archive()
    async with Tether(
        engine=run.engine,
        store=h.store,
        history=h.provider,
        limits=client_limits,
        resume="off",
        archive_sink=sink,
    ) as app:
        client = app.client(h.actor)
        session = await client.create(spec="demo")
        handle = await client.send(session.id, "Fabricated")
        result = await handle.result()
        before = await client.snapshot(session.id)

        async def fail(value):
            raise StoreError("fabricated_sink_failure")

        monkeypatch.setattr(sink, "archive", fail)
        with pytest.raises(StoreError, match="fabricated_sink_failure"):
            await app.archive_session(h.actor, session.id)
        assert await handle.result() == result
        assert await client.snapshot(session.id) == before
        assert len(run.tool.calls) == 1
