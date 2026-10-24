"""Archive source bindings and final disclosure use current caller authority."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_client_reads import CurrentPolicy

from tether.history.jsonl import JsonlHistory
from tether.sessions.archive import ArchiveTransfer, verify_bundle
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.stores.memory import MemoryFiles
from tether.types.canonical import digest
from tether.types.execution import Actor, ExecutionDenied, NewEntry
from tether.types.history import HistoryRecord, HistoryValue
from tether.types.storage import ArchiveBundle, IntegrityError, SessionInit

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(params=["memory", "jsonl"])
async def archive(request, tmp_path):
    files = MemoryFiles() if request.param == "memory" else await JsonlRoot.open(tmp_path)
    store, history = await JsonlSessionStore.open(files), await JsonlHistory.open(files)
    actor, policy = Actor(id="owner"), CurrentPolicy()
    session = await store.create(actor.id, SessionInit())
    record = HistoryRecord(kind="model_exchange", request={"fabricated": "wire"})
    ref = await history.put(actor=actor, session_id=session.id, key="exchange", record=record)
    try:
        yield SimpleNamespace(
            store=store,
            history=history,
            actor=actor,
            policy=policy,
            session=session,
            record=record,
            ref=ref,
            transfer=ArchiveTransfer(store, history, policy),
        )
    finally:
        assert await store.aclose()


async def reference(h, ref):
    async with h.store.turn(h.session.id, h.actor.id, turn_id="reference") as log:
        await log.append([NewEntry(kind="message", history_ref=ref)])


@pytest.mark.parametrize("permission", ["read", "payload"])
async def test_revocation_during_final_snapshot_refuses_assembled_archive(
    archive, monkeypatch, permission
):
    h = archive
    await reference(h, h.ref)
    original = h.store.snapshot
    reads = 0

    async def revoke(session_id, owner):
        nonlocal reads
        result = await original(session_id, owner)
        reads += 1
        if reads == 2:
            setattr(h.policy, permission, False)
        return result

    monkeypatch.setattr(h.store, "snapshot", revoke)
    with pytest.raises(ExecutionDenied, match="archive_denied"):
        await h.transfer.export(h.session.id, h.actor)
    assert reads == 2


async def test_mislabeled_record_cannot_bypass_payload_permission(archive, monkeypatch):
    h = archive
    ref = h.ref.model_copy(update={"kind": "message"})
    await reference(h, ref)
    h.policy.payload = False

    async def faulty(refs, *, actor):
        return tuple(HistoryValue(ref=r, status="ready", record=h.record) for r in refs)

    monkeypatch.setattr(h.history, "read", faulty)
    with pytest.raises(IntegrityError, match="archive_reference_integrity"):
        await h.transfer.export(h.session.id, h.actor)


async def test_bundle_verification_rejects_kind_mismatch_even_with_valid_checksums(archive):
    h = archive
    await reference(h, h.ref)
    bundle = await h.transfer.export(h.session.id, h.actor)
    raw = bundle.model_dump(mode="json")
    raw["session"]["entries"][0]["history_ref"]["kind"] = "message"
    raw["records"][0]["ref"]["kind"] = "message"
    raw["sha256"] = digest({k: v for k, v in raw.items() if k != "sha256"})
    forged = ArchiveBundle.model_validate(raw)
    with pytest.raises(IntegrityError, match="archive_reference_integrity"):
        verify_bundle(forged)


@pytest.mark.parametrize("scope", [{"owner": "other"}, {"session_id": "other"}])
async def test_out_of_scope_refs_refuse_before_history_lookup(archive, monkeypatch, scope):
    h = archive
    await reference(h, h.ref.model_copy(update=scope))

    async def forbidden(*args, **kwargs):
        raise AssertionError("out_of_scope_history_lookup")

    monkeypatch.setattr(h.history, "read", forbidden)
    with pytest.raises(ExecutionDenied, match="archive_denied"):
        await h.transfer.export(h.session.id, h.actor)


async def test_malformed_policy_decision_fails_closed(archive, monkeypatch):
    async def malformed(request):
        return SimpleNamespace(allowed="false")

    monkeypatch.setattr(archive.policy, "authorize", malformed)
    with pytest.raises(ExecutionDenied, match="archive_denied"):
        await archive.transfer.export(archive.session.id, archive.actor)
