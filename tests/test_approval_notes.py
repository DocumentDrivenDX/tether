"""Informational approval notes are preserved as authorized history, never executable input."""

from __future__ import annotations

import pytest
from test_client_reads import harness as harness

from tether.sessions.archive import ArchiveTransfer, verify_bundle
from tether.types.canonical import canonical
from tether.types.execution import Answer, ExecutionDenied
from tether.types.storage import StoreError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("note", ["Fabricated operator annotation.", ""])
async def test_note_content_is_bound_history_outside_audit_and_model_input(harness, note):
    h = harness
    pending = await h.client.pending(h.session.id)
    handle = await h.client.confirm(
        h.session.id, pending.invocation_id, Answer(approved=True, note=note)
    )
    assert handle is not None
    result = await handle.result()
    assert result.stop_reason == "final"
    entries = await h.store.lookup(
        h.session.id, h.actor.id, kind="key", value="approval:" + pending.invocation_id
    )
    assert len(entries) == 1
    decision = entries[0]
    assert decision.history_ref is not None
    assert decision.facts["note_kind"] == "approval_note"
    if note:
        assert note not in canonical(decision.model_dump(mode="json")).decode()
        assert not any(note in m.content for request in h.model.requests for m in request.messages)
        assert not any(note in m.content for m in result.messages)
    h.policy.payload = False
    history = await h.client.history(h.session.id, start=decision.index, limit=1)
    assert len(history.items) == 1
    assert history.items[0].entry_index == decision.index
    assert history.items[0].value.status == "ready"
    assert history.items[0].value.record.message.content == note
    h.policy.payload = True
    bundle = await ArchiveTransfer(h.store, h.history, h.policy).export(h.session.id, h.actor)
    verify_bundle(bundle)
    assert any(raw["ref"] == decision.history_ref.model_dump(mode="json") for raw in bundle.records)
    assert len(h.tool.calls) == 1


async def test_note_write_failure_leaves_approval_pending_and_never_dispatches(
    harness, monkeypatch
):
    h = harness
    pending = await h.client.pending(h.session.id)
    original = h.history.put

    async def reject_note(**kwargs):
        if kwargs["key"].startswith("approval-note:"):
            raise StoreError("fabricated_note_write_failure")
        return await original(**kwargs)

    monkeypatch.setattr(h.history, "put", reject_note)
    with pytest.raises(StoreError, match="fabricated_note_write_failure"):
        await h.client.confirm(
            h.session.id, pending.invocation_id, Answer(approved=True, note="Fabricated note")
        )
    assert (await h.client.pending(h.session.id)).invocation_id == pending.invocation_id
    assert not await h.store.lookup(
        h.session.id, h.actor.id, kind="key", value="approval:" + pending.invocation_id
    )
    assert not h.tool.calls


async def test_engine_note_bound_refuses_without_acceptance_or_dispatch(harness):
    h = harness
    pending = await h.client.pending(h.session.id)
    note = "x" * (h.engine.limits.max_argument_bytes + 1)
    with pytest.raises(ExecutionDenied, match="approval_note_limit"):
        async with h.store.turn(h.session.id, h.actor.id, turn_id="oversized-note") as log:
            await h.engine.answer(
                h.actor, log, pending.invocation_id, Answer(approved=True, note=note)
            )
    assert (await h.client.pending(h.session.id)).invocation_id == pending.invocation_id
    assert not h.tool.calls
