"""Public owner-bound Client reads against the actual Agent and selected history."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_gated_execution import DemoTool, final, proposal

from tether import Tether
from tether.context.testing import fabricated_context
from tether.engine import Engine
from tether.history.jsonl import JsonlHistory
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ScriptedModel
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.stores.memory import MemoryFiles
from tether.types.client import ReadLimit, UnknownModelCall
from tether.types.execution import Actor, AgentSpec, AuthzRequest, Decision, ExecutionDenied
from tether.types.history import HistoryValue
from tether.types.storage import SessionInit, SessionQuery, UnknownSession

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class CurrentPolicy:
    def __init__(self):
        self.read, self.payload = True, True

    async def authorize(self, request: AuthzRequest) -> Decision:
        allowed = (
            self.read
            if request.action == "read_session"
            else self.payload
            if request.action == "read_call_payload"
            else True
        )
        return Decision(allowed=allowed, reason="allowed" if allowed else "denied")


@pytest.fixture(params=["memory", "jsonl"])
async def harness(request, tmp_path, limits, client_limits):
    root = None if request.param == "memory" else await JsonlRoot.open(tmp_path)
    files = root or MemoryFiles()
    store, history = await JsonlSessionStore.open(files), await JsonlHistory.open(files)
    actor, tool, policy = Actor(id="owner"), DemoTool(), CurrentPolicy()
    spec = AgentSpec(
        id="demo",
        version="1",
        title="Demo",
        instructions="Fabricated",
        model="demo",
        tools=(tool.name,),
    )
    model = ScriptedModel([proposal(), final()])
    engine = Engine(
        **fabricated_context(),
        model=model,
        history=MessageHistoryAdapter(history),
        history_provider=history,
        model_limits=limits,
        tools=(tool,),
        specs=(spec,),
        policy=policy,
    )
    session = await store.create(
        actor.id, SessionInit(ref="fabricated", title="Fabricated", settings={"approval": "manual"})
    )
    async with Tether(
        engine=engine,
        store=store,
        history=history,
        limits=client_limits,
        resume="off" if root is None else "report",
    ) as app:
        async with store.turn(session.id, actor.id, turn_id="initial") as log:
            result = await engine.run_turn(actor, log, "Fabricated request", spec=spec)
        assert result.stop_reason == "confirmation_required"
        yield SimpleNamespace(
            app=app,
            client=app.client(actor),
            actor=actor,
            session=session,
            store=store,
            history=history,
            engine=engine,
            model=model,
            tool=tool,
            policy=policy,
        )
    assert app.drained
    if root is not None:
        assert await root.aclose()


async def test_public_metadata_and_hydrated_pending_do_not_write_journal(harness):
    h = harness
    client, sid = h.client, h.session.id
    before = await client.snapshot(sid)
    assert before.view.pending.arguments is None
    assert (await client.get(sid)).summary.id == sid
    assert (await client.list()).items[0].id == sid
    assert (await client.find("fabricated")).id == sid
    assert not (await client.effects(sid)).items
    page = await client.history(sid)
    assert len(page.items) == 2
    assert all(slot.value.status == "ready" for slot in page.items)
    pending = await client.pending(sid)
    assert pending.arguments == {"count": 3, "label": "fabricated-default"}
    assert pending.history_status == "ready"
    assert (
        pending.args_digest
        == h.engine._runs[sid].executor.pending[pending.invocation_id].args_digest
    )
    assert not h.tool.calls
    assert len(h.model.requests) == 1
    assert await client.snapshot(sid) == before


async def test_call_detail_preserves_attempt_phases_and_can_deny_only_payload(harness, monkeypatch):
    h, sid = harness, harness.session.id
    calls = [
        e.facts["model_call_id"]
        for e in (await h.client.entries(sid)).items
        if e.kind == "model_call"
    ]
    details = await h.client.call_detail(sid, calls)
    assert len(details) == 1
    assert details[0].status == "ready"
    assert len(details[0].observations) == 2
    assert len(details[0].receipts) == 1
    assert details[0].receipt == details[0].receipts[0]
    assert details[0].observations[-1].source_locator["receipt_index"] == "0"
    original = h.history.read
    reads = 0

    async def counted(refs, *, actor):
        nonlocal reads
        reads += 1
        return await original(refs, actor=actor)

    monkeypatch.setattr(h.history, "read", counted)
    with pytest.raises(UnknownModelCall):
        await h.client.call_detail(sid, [*calls, "unknown"])
    assert reads == 0
    h.policy.payload = False
    denied = await h.client.call_detail(sid, calls)
    assert denied[0].identity == details[0].identity
    assert denied[0].receipts == details[0].receipts
    assert denied[0].status == "denied"
    assert all(v.record is None for v in denied[0].observations)
    assert reads == 0
    assert (await h.client.history(sid)).items[0].value.status == "ready"


async def test_current_read_policy_and_owner_scope_cover_every_read(harness):
    h, sid = harness, harness.session.id
    other = h.app.client(Actor(id="other"))
    with pytest.raises(UnknownSession):
        await other.get(sid)
    with pytest.raises(UnknownSession):
        await other.get("missing")
    with pytest.raises(ExecutionDenied):
        await other.list(SessionQuery(owner=h.actor.id))
    h.policy.read = False
    for operation in (
        lambda: h.client.get(sid),
        lambda: h.client.snapshot(sid),
        lambda: h.client.pending(sid),
        lambda: h.client.entries(sid),
        lambda: h.client.history(sid),
        lambda: h.client.effects(sid),
        h.client.list,
    ):
        with pytest.raises(ExecutionDenied, match="policy_denied"):
            await operation()


async def test_missing_or_corrupt_sources_are_explicit_not_empty_messages(harness, monkeypatch):
    h, sid = harness, harness.session.id

    async def missing(refs, *, actor):
        return tuple(HistoryValue(ref=r, status="missing") for r in refs)

    monkeypatch.setattr(h.history, "read", missing)
    page = await h.client.history(sid)
    assert all(slot.value.status == "missing" for slot in page.items)
    pending = await h.client.pending(sid)
    assert pending.arguments is None
    assert pending.history_status == "missing"
    assert (await h.client.entries(sid)).items


async def test_revocation_during_provider_read_never_delivers_cached_content(harness, monkeypatch):
    h, sid = harness, harness.session.id
    original = h.history.read

    async def revoke(refs, *, actor):
        values = await original(refs, actor=actor)
        h.policy.read = False
        return values

    monkeypatch.setattr(h.history, "read", revoke)
    with pytest.raises(ExecutionDenied):
        await h.client.history(sid)


async def test_changed_tool_and_read_bounds_refuse_without_dispatch(harness):
    h, sid = harness, harness.session.id
    h.tool.version = "2"
    pending = await h.client.pending(sid)
    assert pending.arguments is None
    assert pending.history_status == "unsupported"
    for limit in (0, -1, True, 101):
        with pytest.raises(ReadLimit):
            await h.client.entries(sid, limit=limit)
    with pytest.raises(ReadLimit):
        await h.client.history(sid, start=-1)
    with pytest.raises(ReadLimit):
        await h.client.call_detail(sid, "arbitrary-provider-id")
    assert not h.tool.calls


async def test_mislabeled_exchange_cannot_bypass_payload_permission(harness, monkeypatch):
    from tether.types.canonical import digest
    from tether.types.execution import HistoryRef, NewEntry
    from tether.types.history import HistoryRecord

    h, sid = harness, harness.session.id
    record = HistoryRecord(kind="model_exchange", request={"fabricated": "wire"})
    ref = HistoryRef(
        provider="fabricated",
        owner=h.actor.id,
        session_id=sid,
        key="malformed",
        codec="tether-history-v1",
        kind="message",
        sha256=digest(record.model_dump(mode="json")),
    )
    async with h.store.turn(sid, h.actor.id, turn_id="fault") as log:
        await log.append([NewEntry(kind="message", history_ref=ref)])
    h.policy.payload = False

    async def bad_provider(refs, *, actor):
        return tuple(HistoryValue(ref=r, status="ready", record=record) for r in refs)

    monkeypatch.setattr(h.history, "read", bad_provider)
    page = await h.client.history(sid)
    assert page.items[-1].value.status == "integrity_error"
    assert page.items[-1].value.record is None


@pytest.mark.parametrize("read_kind", ["calls", "history"])
async def test_payload_revocation_redacts_the_entire_assembled_page(
    harness, monkeypatch, read_kind
):
    from tether.types.execution import NewEntry

    h, sid = harness, harness.session.id
    calls = [
        e.facts["model_call_id"]
        for e in (await h.client.entries(sid)).items
        if e.kind == "model_call"
    ]
    original_details = await h.client.call_detail(sid, calls)
    if read_kind == "history":
        async with h.store.turn(sid, h.actor.id, turn_id="exchange-slots") as log:
            await log.append(
                [
                    NewEntry(kind="message", history_ref=v.ref)
                    for v in original_details[0].observations
                ]
            )
    original = h.history.read
    reads = 0

    async def revoke_later(refs, *, actor):
        nonlocal reads
        values = await original(refs, actor=actor)
        if refs[0].kind == "model_exchange":
            reads += 1
            if reads == 2:
                h.policy.payload = False
        return values

    monkeypatch.setattr(h.history, "read", revoke_later)
    if read_kind == "calls":
        details = await h.client.call_detail(sid, calls)
        assert details[0].identity == original_details[0].identity
        assert details[0].receipts == original_details[0].receipts
        assert details[0].status == "denied"
        values = details[0].observations
    else:
        page = await h.client.history(sid)
        assert page.items[0].value.status == "ready"
        values = tuple(slot.value for slot in page.items if slot.value.ref.kind == "model_exchange")
    assert reads == 2
    assert not h.policy.payload
    assert all(v.status == "denied" and v.record is None for v in values)


async def test_provider_mutation_after_verification_cannot_change_disclosed_content(
    harness, monkeypatch
):
    h, sid = harness, harness.session.id
    retained = []
    original_read, original_authorize = h.history.read, h.policy.authorize
    changed = False

    async def retain(refs, *, actor):
        values = await original_read(refs, actor=actor)
        retained.extend(values)
        return values

    async def mutate(request):
        nonlocal changed
        for value in retained:
            record = value.record
            if record is not None and record.message is not None and record.message.tool_calls:
                record.message.tool_calls[0].arguments["count"] = "999"
                changed = True
        return await original_authorize(request)

    monkeypatch.setattr(h.history, "read", retain)
    monkeypatch.setattr(h.policy, "authorize", mutate)
    page = await h.client.history(sid)
    assert changed
    calls = page.items[-1].value.record.message.tool_calls
    assert calls[0].arguments["count"] == "3"
