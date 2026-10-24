"""Client observation preserves known acceptance, current identity and disclosure gates."""

import asyncio
from types import SimpleNamespace

import pytest
from test_telemetry_ports import id_factory

from tether import Tether
from tether.context.testing import fabricated_context
from tether.engine import Engine
from tether.history.jsonl import JsonlHistory
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ScriptedModel
from tether.sessions.archive import ArchiveTransfer
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.stores.memory import MemoryFiles
from tether.testing.telemetry import ListAuditSink, RecordingTracer
from tether.types.canonical import digest
from tether.types.client import ReadLimit
from tether.types.execution import Actor, CommitUnknown, Decision, ExecutionDenied, NewEntry
from tether.types.storage import SessionInit, StoreError, Unsupported

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Policy:
    def __init__(self):
        self.read = True
        self.requests = []

    async def authorize(self, request):
        self.requests.append(request)
        return Decision(allowed=self.read, reason="allowed" if self.read else "denied")


@pytest.fixture(params=["memory", "jsonl"])
async def observed(request, tmp_path, limits, client_limits):
    root = await JsonlRoot.open(tmp_path) if request.param == "jsonl" else MemoryFiles()
    store, history = await JsonlSessionStore.open(root), await JsonlHistory.open(root)
    policy, sink = Policy(), ListAuditSink(capacity=512)
    tracer = RecordingTracer(id_factory(), capacity=512)
    engine = Engine(
        **fabricated_context(),
        model=ScriptedModel([]),
        history=MessageHistoryAdapter(history),
        history_provider=history,
        model_limits=limits,
        policy=policy,
        audit=sink,
        tracer=tracer,
    )
    actor = Actor(
        id="owner", principal="current", attributes={"private_note": "actor attribute marker"}
    )
    app = Tether(engine=engine, store=store, history=history, limits=client_limits, resume="off")
    await app.__aenter__()
    h = SimpleNamespace(
        root=root,
        store=store,
        history=history,
        policy=policy,
        sink=sink,
        tracer=tracer,
        engine=engine,
        app=app,
        actor=actor,
        client=app.client(actor),
    )
    yield h
    assert await app.aclose()
    assert await root.aclose()


def semantic(h):
    return [r for r in h.sink.records if r.kind in {"session_started", "session_settings"}]


async def test_client_create_settings_and_reads_have_exact_private_safe_context(observed):
    h = observed
    made = await h.client.create(ref="reference content marker", title="title content marker")
    await h.client.update(
        made.id,
        tags=["tag content marker"],
        settings={"setting key marker": "setting value marker"},
    )
    await h.client.snapshot(made.id)
    entries = (await h.store.entries(made.id, h.actor.id, start=0, limit=3)).items
    assert await h.app.aclose()
    events = semantic(h)
    assert len(events) == 3
    genesis = next(r for r in events if r.kind == "session_started")
    assert genesis.context.event_id == digest(["tether.audit.v1", "genesis", h.actor.id, made.id])
    assert "entry_position" not in genesis.detail
    metadata = [r for r in events if r.kind == "session_settings"]
    assert [r.detail["category"] for r in metadata] == ["tags", "settings"]
    for row, entry in zip(metadata, entries, strict=True):
        assert row.context.event_id == digest(
            ["tether.audit.v1", h.actor.id, made.id, entry.index, "session_settings"]
        )
        assert row.detail["entry_position"] == entry.index
    gates = [r for r in h.sink.records if r.kind == "authorization"]
    assert gates
    assert all(r.detail["origin"] == "client" for r in gates)
    assert all("entry_position" not in r.detail for r in gates)
    assert any(r.context.session_id == made.id for r in gates)
    assert all(
        r.context.actor == h.actor.id and r.context.principal == "current" for r in h.sink.records
    )
    assert "content marker" not in " ".join(r.model_dump_json() for r in h.sink.records)
    assert "setting key marker" not in " ".join(r.model_dump_json() for r in h.sink.records)
    assert "actor attribute marker" not in " ".join(r.model_dump_json() for r in h.sink.records)
    gate_spans = [r for r in h.tracer.records if r.name == "tether.gate"]
    assert gate_spans
    assert all(r.finished for r in gate_spans)
    assert all(r.attributes["tether.allowed"] is True for r in gate_spans)


async def test_same_reference_after_turn_and_updates_emits_one_genesis(observed):
    h = observed
    results = await asyncio.gather(*[h.client.create(ref="fabricated") for _ in range(3)])
    sid = results[0].id
    assert all(r.id == sid for r in results)
    async with h.store.turn(sid, h.actor.id, turn_id="intervening") as log:
        await log.append(
            [
                NewEntry(kind="turn_started", turn_id="intervening"),
                NewEntry(kind="turn_ended", turn_id="intervening", facts={"stop_reason": "final"}),
            ]
        )
    await h.client.update(sid, title="Changed")
    again = await h.client.create(ref="fabricated")
    assert again.title == "Changed"
    assert again.entry_count == 3
    await h.client.update(sid)
    await h.client.update(sid, title="Changed")
    assert await h.app.aclose()
    assert sum(r.kind == "session_started" for r in semantic(h)) == 1
    rows = [r for r in semantic(h) if r.kind == "session_settings"]
    assert [r.detail["entry_position"] for r in rows] == [2, 3]
    assert rows[0].context.event_id != rows[1].context.event_id


@pytest.mark.parametrize("operation", ["create", "update"])
async def test_later_disclosure_revocation_does_not_erase_known_acceptance(
    observed, monkeypatch, operation
):
    h = observed
    prior = await h.store.create(h.actor.id, SessionInit(ref="existing"))
    name = operation + "_with_receipt"
    original = getattr(h.store, name)

    async def revoke(*args):
        receipt = await original(*args)
        h.policy.read = False
        return receipt

    monkeypatch.setattr(h.store, name, revoke)
    call = (
        h.client.create(ref="new", title="Fabricated")
        if operation == "create"
        else h.client.update(prior.id, title="Fabricated")
    )
    with pytest.raises(ExecutionDenied):
        await call
    assert await h.app.aclose()
    rows = semantic(h)
    assert len(rows) == 1
    assert rows[0].kind == ("session_started" if operation == "create" else "session_settings")
    assert any(r.detail.get("allowed") is False for r in h.sink.records)


@pytest.mark.parametrize(
    "error", ["unknown", "version", "owner", "reference", "tail", "wrong_session"]
)
async def test_bad_receipts_never_trigger_semantic_event_or_legacy_retry(
    observed, monkeypatch, error
):
    h = observed
    prior = await h.store.create(h.actor.id, SessionInit(ref="existing"))
    update = error in {"tail", "wrong_session"}
    method = "update_with_receipt" if update else "create_with_receipt"
    original = getattr(h.store, method)
    calls = []

    async def malformed(*args):
        calls.append(True)
        receipt = await original(*args)
        if error == "unknown":
            raise CommitUnknown("private unknown marker")
        if error == "version":
            return receipt.model_copy(update={"version": True})
        summary = receipt.summary
        if error in {"owner", "reference", "wrong_session"}:
            field = "owner" if error == "owner" else "ref" if error == "reference" else "id"
            summary = summary.model_copy(update={field: "foreign"})
            return receipt.model_copy(update={"summary": summary})
        commit = receipt.commit
        assert commit is not None
        entry = commit.entries[0].model_copy(update={"index": 99})
        return receipt.model_copy(
            update={"commit": commit.model_copy(update={"entries": (entry,)})}
        )

    async def legacy(*args):
        raise AssertionError("legacy retry")

    monkeypatch.setattr(h.store, method, malformed)
    monkeypatch.setattr(h.store, "update" if update else "create", legacy)
    call = h.client.update(prior.id, title="Fabricated") if update else h.client.create(ref="new")
    with pytest.raises((StoreError, CommitUnknown)):
        await call
    assert calls == [True]
    assert await h.app.aclose()
    assert not semantic(h)


@pytest.mark.parametrize("receipt_limit", [True, False])
async def test_receipt_bound_is_distinct_from_later_summary_bound(observed, receipt_limit):
    h = observed
    if receipt_limit:
        h.app.access.limits = h.app.limits.model_copy(update={"max_mutation_receipt_bytes": 10})
    else:
        h.app.access.limits = h.app.limits.model_copy(update={"max_read_bytes": 10})
    with pytest.raises(
        ReadLimit, match="mutation_receipt_bytes" if receipt_limit else "read_bytes"
    ):
        await h.client.create(ref="accepted", title="Fabricated")
    assert await h.app.aclose()
    assert bool(semantic(h)) is (not receipt_limit)
    assert (h.actor.id, "accepted") in h.store.refs


async def test_legacy_capability_has_only_successful_return_observations(observed):
    h = observed
    h.store.capabilities = h.store.capabilities.model_copy(update={"mutation_receipts": 0})
    made = await h.client.create(ref="fabricated")
    await h.client.update(made.id, title="Changed")
    assert await h.app.aclose()
    assert not semantic(h)
    rows = [r for r in h.sink.records if r.kind.startswith("x.client.")]
    assert {r.kind for r in rows} == {"x.client.create_returned", "x.client.metadata_returned"}
    assert all(r.detail["semantic_receipts"] is False for r in rows)
    assert all("entry_position" not in r.detail and "commit_seq" not in r.detail for r in rows)


async def test_legacy_failed_disclosure_has_no_returned_observation(observed, monkeypatch):
    h = observed
    h.store.capabilities = h.store.capabilities.model_copy(update={"mutation_receipts": 0})
    original = h.store.create

    async def create(*args):
        summary = await original(*args)
        h.policy.read = False
        return summary

    monkeypatch.setattr(h.store, "create", create)
    with pytest.raises(ExecutionDenied):
        await h.client.create(ref="fabricated")
    assert await h.app.aclose()
    assert not semantic(h)
    assert not any(r.kind.startswith("x.client.") for r in h.sink.records)


async def test_policy_cancel_has_no_fabricated_completed_gate(observed, monkeypatch):
    h = observed

    async def cancel(request):
        raise asyncio.CancelledError("policy cancellation marker")

    monkeypatch.setattr(h.policy, "authorize", cancel)
    with pytest.raises(asyncio.CancelledError):
        await h.client.create(ref="never")
    assert await h.app.aclose()
    assert not h.sink.records
    span = next(r for r in h.tracer.records if r.name == "tether.gate")
    assert span.finished
    assert span.error_reason == "cancelled"


async def test_policy_failure_is_closed_and_reason_content_is_excluded(observed, monkeypatch):
    h = observed

    async def fail(request):
        raise RuntimeError("policy exception marker")

    monkeypatch.setattr(h.policy, "authorize", fail)
    with pytest.raises(ExecutionDenied):
        await h.client.create(ref="never")
    assert await h.app.aclose()
    rows = [r for r in h.sink.records if r.kind == "authorization"]
    assert len(rows) == 1
    assert rows[0].detail["reason"] == "policy_unavailable"
    assert rows[0].detail["allowed"] is False
    assert "policy exception marker" not in rows[0].model_dump_json()


async def test_capability_mismatch_refuses_before_write(observed, monkeypatch):
    h = observed
    monkeypatch.setattr(h.store, "create_with_receipt", None)
    with pytest.raises(Unsupported):
        await h.client.create(ref="never")
    assert not h.store.index
    assert await h.app.aclose()
    assert not semantic(h)


@pytest.mark.parametrize("target", ["client", "archive"])
@pytest.mark.parametrize("allowed", [{"private": "policy value marker"}, "true", 1])
async def test_malformed_typed_policy_response_denies_without_content(
    observed, monkeypatch, target, allowed
):
    h = observed
    made = await h.store.create(h.actor.id, SessionInit(ref="existing"))

    async def malformed(request):
        return Decision(allowed=True, reason="allowed").model_copy(update={"allowed": allowed})

    monkeypatch.setattr(h.policy, "authorize", malformed)
    call = (
        h.client.create(ref="never")
        if target == "client"
        else ArchiveTransfer(h.store, h.history, h.policy, observers=h.engine.observers).export(
            made.id, h.actor
        )
    )
    with pytest.raises(ExecutionDenied):
        await call
    assert (h.actor.id, "never") not in h.store.refs
    assert await h.app.aclose()
    rows = [r for r in h.sink.records if r.kind == "authorization"]
    assert len(rows) == 1
    assert rows[0].detail["allowed"] is False
    assert rows[0].detail["reason"] == "policy_unavailable"
    assert not semantic(h)
    assert "policy value marker" not in repr(h.sink.records) + repr(h.tracer.records)
    assert all(
        span.attributes["tether.allowed"] is False
        for span in h.tracer.records
        if span.name == "tether.gate"
    )


async def test_gate_metadata_independently_refuses_nonboolean_values(observed):
    h = observed
    observers = h.engine.observers
    before = observers.failures
    with observers.client.gate(h.actor, "resource", "read_session", None) as gate:
        gate.completed({"private": "gate value marker"}, "allowed")
    assert observers.failures == before + 1
    assert await h.app.aclose()
    assert not h.sink.records
    assert "gate value marker" not in repr(h.tracer.records)
    assert all("tether.allowed" not in span.attributes for span in h.tracer.records)


async def test_cancelled_final_disclosure_retains_known_semantic_receipt(observed, monkeypatch):
    h = observed
    entered, release = asyncio.Event(), asyncio.Event()
    original = h.policy.authorize

    async def policy(request):
        if not request.resource.startswith("owner:"):
            entered.set()
            await release.wait()
        return await original(request)

    monkeypatch.setattr(h.policy, "authorize", policy)
    task = asyncio.create_task(h.client.create(ref="accepted"))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert (h.actor.id, "accepted") in h.store.refs
    assert await h.app.aclose()
    assert len(semantic(h)) == 1
    gates = [r for r in h.sink.records if r.kind == "authorization"]
    assert len(gates) == 1  # Initial gate only; cancelled gate has no completed decision.
    command = next(r for r in h.tracer.records if r.name == "tether.client.create")
    assert command.error_reason == "cancelled"
    assert semantic(h)[0].context.span_id == command.ids.span_id


async def test_concurrent_current_identities_isolate_policy_mutation(observed, monkeypatch):
    from test_client_resume import FreshCredential

    h = observed
    credentials = [FreshCredential(), FreshCredential()]
    actors = [
        h.actor.model_copy(update={"principal": principal, "credential": credential})
        for principal, credential in zip(("first", "second"), credentials, strict=True)
    ]
    arrived, release = asyncio.Event(), asyncio.Event()
    seen = []

    async def policy(request):
        seen.append((request.actor.principal, request.actor.credential))
        request.actor.attributes["private_note"] = "mutated policy marker"
        if request.resource.startswith("owner:"):
            if len(seen) == 2:
                arrived.set()
            await release.wait()
        return Decision(allowed=True, reason="private reason marker")

    monkeypatch.setattr(h.policy, "authorize", policy)
    tasks = [
        asyncio.create_task(h.app.client(actor).create(ref=actor.principal)) for actor in actors
    ]
    try:
        await asyncio.wait_for(arrived.wait(), 1)
        release.set()
        made = await asyncio.gather(*tasks)
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert all(actor.attributes["private_note"] == "actor attribute marker" for actor in actors)
    assert all(
        credential is credentials[("first", "second").index(principal)]
        for principal, credential in seen
    )
    assert await h.app.aclose()
    assert {r.context.session_id: r.context.principal for r in semantic(h)} == {
        summary.id: actor.principal for summary, actor in zip(made, actors, strict=True)
    }
    assert "private reason marker" not in repr(h.sink.records) + repr(h.tracer.records)
    for command in [r for r in h.tracer.records if r.name == "tether.client.create"]:
        assert any(r.parent == command.ids for r in h.tracer.records if r.name == "tether.gate")


async def test_client_hung_sink_does_not_hold_mutation_or_root(tmp_path, limits, client_limits):
    from contextlib import suppress

    from tether.types.telemetry import TelemetryLimits

    entered, release = asyncio.Event(), asyncio.Event()

    class Sink:
        async def emit(self, **kwargs):
            entered.set()
            while not release.is_set():
                with suppress(asyncio.CancelledError):
                    await release.wait()

    root = await JsonlRoot.open(tmp_path)
    store, history = await JsonlSessionStore.open(root), await JsonlHistory.open(root)
    engine = Engine(
        **fabricated_context(),
        model=ScriptedModel([]),
        history=MessageHistoryAdapter(history),
        history_provider=history,
        model_limits=limits,
        audit=Sink(),
        telemetry_limits=TelemetryLimits(
            delivery_timeout_seconds=0.01, shutdown_timeout_seconds=0.01
        ),
    )
    app = Tether(engine=engine, store=store, history=history, limits=client_limits, resume="off")
    await app.__aenter__()
    try:
        client = app.client(Actor(id="owner"))
        made = await asyncio.wait_for(client.create(ref="fabricated"), 1)
        changed = await asyncio.wait_for(client.update(made.id, title="Changed"), 1)
        assert changed.title == "Changed"
        await asyncio.wait_for(entered.wait(), 1)
        assert not await app.aclose()
        reopened = await JsonlRoot.open(tmp_path)
        assert await reopened.aclose()
    finally:
        release.set()
        if not app.closed:
            await app.aclose()
        await asyncio.wait_for(engine.wait_observers_closed(), 1)
        if app._cleanup is not None:
            await asyncio.wait_for(app._cleanup, 1)
        assert app.drained
        assert await root.aclose()


async def test_client_sink_failure_and_mutation_are_secondary(observed, monkeypatch):
    h = observed
    seen = []

    async def emit(**packet):
        seen.append(packet["kind"])
        packet["detail"].clear()
        raise RuntimeError("sink failure marker")

    monkeypatch.setattr(h.sink, "emit", emit)
    made = await h.client.create(ref="fabricated")
    changed = await h.client.update(made.id, title="Changed")
    entries = (await h.store.entries(made.id, h.actor.id, start=0, limit=3)).items
    assert changed.title == "Changed"
    assert len(entries) == 1
    assert entries[0].facts["value"] == "Changed"
    assert await h.app.aclose()
    assert "session_started" in seen
    assert "session_settings" in seen
    assert h.engine.audit_stats.failed == len(seen)
    assert "sink failure marker" not in repr(h.tracer.records)


async def test_archive_list_owner_gate_has_no_invented_session_identity(observed):
    from tether.sessions.archived import ArchiveAccess
    from tether.stores.archive import InMemoryArchive
    from tether.types.archive import ArchivedSession, ArchiveLimits

    h = observed
    made = await h.store.create(h.actor.id, SessionInit(ref="fabricated"))
    bundle = await ArchiveTransfer(h.store, h.history, h.policy).export(made.id, h.actor)
    view = await h.store.get(made.id, h.actor.id)
    archive = InMemoryArchive(ArchiveLimits(max_sessions=2, max_entries=10, max_bytes=1048576))
    await archive.archive(
        ArchivedSession(
            view=view.model_copy(
                update={"summary": view.summary.model_copy(update={"tier": "archive"})}
            ),
            bundle=bundle,
        )
    )
    h.app.archives = ArchiveAccess(h.app.access, archive, archive)
    client = h.app.client(h.actor)
    assert [s.id for s in (await client.list(tier="archive", limit=2)).items] == [made.id]
    assert (await client.get(made.id, tier="archive")).summary.id == made.id
    assert await h.app.aclose()
    gates = [r for r in h.sink.records if r.kind == "authorization"]
    owner_gates = [r for r in gates if r.detail["resource_digest"] == digest("owner:" + h.actor.id)]
    assert len(owner_gates) == 2
    assert all(r.context.session_id is None for r in owner_gates)
    item_gates = [r for r in gates if r.detail["resource_digest"] == digest(made.id)]
    assert item_gates
    assert all(r.context.session_id == made.id for r in item_gates)


async def test_owner_wide_follow_item_gates_use_exact_session_identity(observed):
    from contextlib import aclosing

    from tether.types.storage import SessionPatch

    h = observed
    made = await h.store.create(h.actor.id, SessionInit(ref="fabricated"))
    baseline = await h.store.watermark(h.actor.id)
    await h.store.update(made.id, h.actor.id, SessionPatch(title="Changed"))
    async with aclosing(h.client.follow(since=baseline)) as stream:
        value = await asyncio.wait_for(anext(stream), 1)
        assert value.summary.id == made.id
    assert await h.app.aclose()
    gates = [r for r in h.sink.records if r.kind == "authorization"]
    owner_gates = [r for r in gates if r.detail["resource_digest"] == digest("owner:" + h.actor.id)]
    assert owner_gates
    assert all(r.context.session_id is None for r in owner_gates)
    item_gates = [r for r in gates if r.detail["resource_digest"] == digest(made.id)]
    assert item_gates
    assert all(r.context.session_id == made.id for r in item_gates)
