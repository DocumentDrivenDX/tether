"""Atomic local/volatile receipt semantics and optional actor-bound compatibility."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from pydantic import ValidationError
from test_resume_engine import recovery as recovery

from tether.ports.bound import ActorStore
from tether.testing.conformance import LiveStoreFixture, mutation_receipt_suite
from tether.types.execution import Actor, CommitUnknown, Entry, ExecutionDenied, NewEntry
from tether.types.mutations import CreateReceipt, MutationCommit, UpdateReceipt, receipt_store
from tether.types.storage import (
    SessionInit,
    SessionPatch,
    SessionStore,
    SessionSummary,
    StoreCapabilities,
    StoreError,
    Unsupported,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def port(store, actor):
    selected = receipt_store(store.for_actor(actor))
    assert selected is not None
    return selected


async def test_public_companion_suite_on_local_and_volatile_dummy(recovery):
    h = recovery

    @asynccontextmanager
    async def factory():
        yield LiveStoreFixture(h.store, h.actor, Actor(id="foreign"))

    report = await mutation_receipt_suite(factory)
    assert report.suite == "mutation_receipts"
    assert "one_genesis" in report.checks
    assert "exact_accepted_metadata" in report.checks
    assert "mutation_receipt_unknown_ack" in report.required_external


async def test_update_receipt_is_captured_before_release_allows_another_writer(
    recovery, monkeypatch
):
    h = recovery
    selected = port(h.store, h.actor)
    session = await selected.create_with_receipt(h.actor.id, SessionInit())
    original, fired = h.store.release, False

    async def release(sid, owner, generation):
        nonlocal fired
        await original(sid, owner, generation)
        if not fired:
            fired = True
            await selected.update_with_receipt(sid, owner, SessionPatch(title="Later"))

    monkeypatch.setattr(h.store, "release", release)
    first = await selected.update_with_receipt(
        session.summary.id, h.actor.id, SessionPatch(title="First")
    )
    assert first.summary.title == "First"
    assert first.summary.entry_count == 1
    assert first.commit is not None
    assert first.commit.entries[0].index == 0
    latest = await h.store.get(session.summary.id, h.actor.id)
    assert latest is not None
    assert latest.summary.title == "Later"
    assert latest.summary.last_seq > first.summary.last_seq


@pytest.mark.parametrize("method", ["create", "update"])
async def test_unknown_journal_ack_never_returns_receipt(recovery, monkeypatch, method):
    h = recovery
    selected = port(h.store, h.actor)
    session = await selected.create_with_receipt(h.actor.id, SessionInit(ref="fabricated"))
    original = h.store.files.append_file

    async def unknown(name, value, *, guard=None):
        result = await original(name, value, guard=guard)
        if isinstance(value, dict) and value.get("operation") == (
            "create" if method == "create" else "append"
        ):
            raise CommitUnknown("fabricated unknown acknowledgment")
        return result

    monkeypatch.setattr(h.store.files, "append_file", unknown)
    operation = (
        selected.create_with_receipt(h.actor.id, SessionInit(ref="other"))
        if method == "create"
        else selected.update_with_receipt(
            session.summary.id, h.actor.id, SessionPatch(title="Accepted but unknown")
        )
    )
    with pytest.raises(CommitUnknown):
        await operation


async def test_bound_receipt_owner_close_and_copy_isolation(recovery):
    h = recovery
    scope = h.store.for_actor(h.actor)
    selected = receipt_store(scope)
    assert selected is not None
    with pytest.raises(ExecutionDenied):
        await selected.create_with_receipt("foreign", SessionInit())
    made = await selected.create_with_receipt(h.actor.id, SessionInit())
    with pytest.raises(ExecutionDenied):
        await selected.update_with_receipt(made.summary.id, "foreign", SessionPatch())
    result = await selected.update_with_receipt(
        made.summary.id, h.actor.id, SessionPatch(settings={"nested": {"value": 1}})
    )
    assert result.commit is not None
    result.commit.entries[0].facts["changes"] = {"nested": "changed"}
    view = await scope.get(made.summary.id, h.actor.id)
    assert view is not None
    assert view.settings["nested"] == {"value": 1}
    assert await scope.aclose()
    with pytest.raises(RuntimeError, match="store_scope_closed"):
        await selected.create_with_receipt(h.actor.id, SessionInit())
    with pytest.raises(RuntimeError, match="store_scope_closed"):
        await selected.update_with_receipt(made.summary.id, h.actor.id, SessionPatch())
    assert await h.store.for_actor(h.actor).get(made.summary.id, h.actor.id) is not None


async def test_optional_capability_absence_and_mismatch_refuse_without_writes(recovery):
    h = recovery
    before = await h.store.watermark(h.actor.id)
    h.store.capabilities = h.store.capabilities.model_copy(update={"mutation_receipts": 0})
    assert receipt_store(h.store.for_actor(h.actor)) is None
    scope = ActorStore(h.store, h.actor)
    with pytest.raises(Unsupported):
        await scope.create_with_receipt(h.actor.id, SessionInit())
    with pytest.raises(Unsupported):
        await scope.update_with_receipt("missing", h.actor.id, SessionPatch())
    h.store.capabilities = h.store.capabilities.model_copy(update={"mutation_receipts": 2})
    with pytest.raises(Unsupported, match="version"):
        receipt_store(h.store)
    assert await h.store.watermark(h.actor.id) == before


async def test_lying_companion_capability_is_detected_before_dispatch():
    class Missing:
        capabilities = StoreCapabilities(
            durability="none", lease="process", fencing=True, mutation_receipts=1
        )

    with pytest.raises(Unsupported):
        receipt_store(cast(SessionStore, Missing()))


def summary(count=0):
    return SessionSummary(
        id="session",
        owner="owner",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        last_seq=1,
        entry_count=count,
    )


def entry(index=0, kind="settings", facts=None):
    return Entry(
        index=index,
        at=datetime.now(UTC),
        kind=kind,
        facts={"changes": {"fabricated": True}} if facts is None else facts,
    )


@pytest.mark.parametrize("version", [True, False, "1", 0, 2])
async def test_receipt_and_capability_version_refuse_coercion(version):
    with pytest.raises(ValidationError):
        CreateReceipt.model_validate(
            {"version": version, "summary": summary(), "disposition": "existing"}
        )
    values = {
        "durability": "none",
        "lease": "process",
        "fencing": True,
        "mutation_receipts": version,
    }
    if type(version) is int and version == 0:
        assert StoreCapabilities.model_validate(values).mutation_receipts == 0
    else:
        with pytest.raises(ValidationError):
            StoreCapabilities.model_validate(values)


@pytest.mark.parametrize(
    "change", ["owner", "session", "seq", "positions", "empty", "executable", "event", "too_many"]
)
async def test_receipts_refuse_incoherent_scope_frontier_and_metadata(change):
    commit = {"owner": "owner", "session_id": "session", "seq": 1, "entries": [entry()]}
    if change == "owner":
        commit["owner"] = "other"
    elif change == "session":
        commit["session_id"] = "other"
    elif change == "seq":
        commit["seq"] = 2
    elif change == "positions":
        commit["entries"] = [entry(index=2)]
    elif change == "empty":
        commit["entries"] = []
    elif change == "executable":
        commit["entries"] = [entry(kind="tool_call")]
    elif change == "event":
        commit["entries"] = [entry(kind="session", facts={"event": {"invalid": True}})]
    else:
        commit["entries"] = [entry(index=i) for i in range(4)]
    with pytest.raises(ValidationError):
        UpdateReceipt.model_validate(
            {"summary": summary(1), "disposition": "accepted", "commit": commit}
        )


@pytest.mark.parametrize("kind", ["existing", "created", "noop"])
async def test_disposition_requires_correct_commit_presence(kind):
    commit = MutationCommit(owner="owner", session_id="session", seq=1)
    values = {"summary": summary(), "disposition": kind}
    if kind != "created":
        values["commit"] = commit
    model = UpdateReceipt if kind == "noop" else CreateReceipt
    with pytest.raises(ValidationError):
        model.model_validate(values)


async def test_empty_patch_can_recover_expired_phase_without_a_metadata_commit(
    recovery, monkeypatch
):
    h = recovery
    selected = port(h.store, h.actor)
    made = await selected.create_with_receipt(h.actor.id, SessionInit())
    async with h.store.turn(made.summary.id, h.actor.id, turn_id="old") as log:
        await log.append([NewEntry(kind="turn_started", turn_id="old")])
        before = await h.store.snapshot(made.summary.id, h.actor.id)
        later = h.store.clock() + timedelta(seconds=60)
        monkeypatch.setattr(h.store, "clock", lambda: later)
        monkeypatch.setattr(h.store.leases, "clock", lambda: later)
        receipt = await selected.update_with_receipt(made.summary.id, h.actor.id, SessionPatch())
    assert receipt.disposition == "noop"
    assert receipt.commit is None
    assert receipt.summary.last_seq > before.view.summary.last_seq
    assert receipt.summary.status == "interrupted"
    assert receipt.summary.entry_count == before.view.summary.entry_count + 1


async def test_mutation_receipt_wrong_loop_refuses_before_storage(recovery):
    h = recovery
    selected = port(h.store, h.actor)
    before = await h.store.watermark(h.actor.id)

    def elsewhere():
        return asyncio.run(selected.create_with_receipt(h.actor.id, SessionInit()))

    with pytest.raises(RuntimeError, match="wrong_event_loop"):
        await asyncio.to_thread(elsewhere)
    assert await h.store.watermark(h.actor.id) == before


async def test_mutation_values_obey_selected_adapter_bounds(recovery):
    h = recovery
    selected = port(h.store, h.actor)
    made = await selected.create_with_receipt(h.actor.id, SessionInit())
    h.store.files.limits = h.store.files.limits.model_copy(
        update={"max_index_bytes": 8192, "max_batch_bytes": 8192}
    )
    with pytest.raises(StoreError):
        await selected.update_with_receipt(
            made.summary.id, h.actor.id, SessionPatch(title="x" * 16384)
        )
    view = await h.store.get(made.summary.id, h.actor.id)
    assert view is not None
    assert view.summary.title is None


async def test_conformance_detects_duplicate_genesis_negative_control(recovery):
    from tether.testing.conformance import ConformanceFailure

    h = recovery
    original = h.store.create_with_receipt

    async def duplicate(owner, init):
        value = await original(owner, init)
        return value.model_copy(update={"disposition": "created"})

    h.store.create_with_receipt = duplicate

    @asynccontextmanager
    async def factory():
        yield LiveStoreFixture(h.store, h.actor, Actor(id="foreign"))

    with pytest.raises(ConformanceFailure, match="one_genesis"):
        await mutation_receipt_suite(factory)


async def test_conformance_detects_wrong_receipt_sequence_negative_control(recovery):
    from tether.testing.conformance import ConformanceFailure

    h = recovery
    original = h.store.update_with_receipt

    async def corrupt(sid, owner, patch):
        receipt = await original(sid, owner, patch)
        if receipt.commit is not None:
            receipt = receipt.model_copy(
                update={"commit": receipt.commit.model_copy(update={"seq": receipt.commit.seq + 1})}
            )
        return receipt

    h.store.update_with_receipt = corrupt

    @asynccontextmanager
    async def factory():
        yield LiveStoreFixture(h.store, h.actor, Actor(id="foreign"))

    with pytest.raises(ConformanceFailure, match="receipt_commit_scope"):
        await mutation_receipt_suite(factory)
