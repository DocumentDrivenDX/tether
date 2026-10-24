"""Atomic input acceptance and supersession precede any next Agent dispatch."""

from __future__ import annotations

import pytest
from test_client_reads import harness as harness

from tether.types.canonical import digest
from tether.types.execution import ExecutionDenied, TurnStart
from tether.types.storage import JournalEvent

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_acceptance_uses_supplied_turn_id_and_atomic_supersession(harness):
    h, sid = harness, harness.session.id
    pending = await h.client.pending(sid)
    spec = h.engine.spec("demo")
    start = TurnStart(turn_id="accepted-turn", request_id="fabricated-request")
    signaled = []
    async with h.store.turn(sid, h.actor.id, turn_id=start.turn_id) as log:

        def accepted():
            entries = log.entries
            assert any(e.key == "request:fabricated-request" for e in entries)
            assert any(e.key == "result:" + pending.invocation_id for e in entries)
            signaled.append(True)

        result = await h.engine.run_turn(
            h.actor, log, "Fabricated next input", spec=spec, start=start, accepted=accepted
        )
    assert signaled == [True]
    assert result.turn_id == start.turn_id
    assert not h.tool.calls
    assert await h.client.pending(sid) is None
    accepted_batch = None
    async for record in h.store.files.scan("journal"):
        event = JournalEvent.model_validate(record.value)
        if any(e.key == "request:fabricated-request" for e in event.entries):
            accepted_batch = event.entries
    assert accepted_batch is not None
    request = next(e for e in accepted_batch if e.key == "request:fabricated-request")
    assert request.facts["input_digest"] == digest(
        {"text": "Fabricated next input", "attachments": [], "spec": None}
    )
    assert any(e.key == "result:" + pending.invocation_id for e in accepted_batch)
    assert any(e.key == "approval:" + pending.invocation_id for e in accepted_batch)
    result_entry = next(e for e in accepted_batch if e.key == "result:" + pending.invocation_id)
    assert result_entry.turn_id != start.turn_id
    assert request.turn_id == start.turn_id


async def test_input_content_failure_does_not_supersede_pending(harness, monkeypatch):
    h, sid = harness, harness.session.id
    before = await h.client.pending(sid)
    original = h.engine.history.put

    async def fail_user(**values):
        if values["key"].startswith("user:"):
            raise OSError("fabricated_content_failure")
        return await original(**values)

    monkeypatch.setattr(h.engine.history, "put", fail_user)
    signaled = []
    async with h.store.turn(sid, h.actor.id, turn_id="refused-input") as log:
        with pytest.raises(OSError, match="fabricated_content_failure"):
            await h.engine.run_turn(
                h.actor,
                log,
                "Fabricated",
                spec=h.engine.spec("demo"),
                accepted=lambda: signaled.append(True),
            )
    assert not signaled
    assert (await h.client.pending(sid)).invocation_id == before.invocation_id
    assert not await h.store.lookup(
        sid, h.actor.id, kind="key", value="approval:" + before.invocation_id
    )
    assert len(h.model.requests) == 1


async def test_missing_or_ambiguous_pinned_spec_does_not_guess(harness):
    h = harness
    with pytest.raises(ExecutionDenied, match="spec_unavailable"):
        h.engine.spec("missing")
    one = h.engine.spec("demo")
    assert one.id == "demo"
    # Simulate the explicit registry containing two versions; no version is selected implicitly.
    h.engine._specs["fabricated-other-version"] = one.model_copy(update={"version": "2"})
    with pytest.raises(ExecutionDenied, match="spec_unavailable"):
        h.engine.spec("demo")


async def test_reused_turn_identity_refuses_without_model_dispatch(harness):
    h, sid = harness, harness.session.id
    entries = (await h.client.entries(sid)).items
    original = next(e.turn_id for e in entries if e.kind == "turn_started")
    async with h.store.turn(sid, h.actor.id, turn_id="duplicate-test") as log:
        with pytest.raises(ExecutionDenied, match="turn_already_started"):
            await h.engine.run_turn(
                h.actor,
                log,
                "Fabricated duplicate",
                spec=h.engine.spec("demo"),
                start=TurnStart(turn_id=original),
            )
    assert len(h.model.requests) == 1
    assert (await h.client.pending(sid)) is not None


async def test_orphan_actual_result_cannot_block_unknown_closure(harness, monkeypatch):
    from tether.types.execution import Answer
    from tether.types.storage import StoreError

    h, sid = harness, harness.session.id
    pending = await h.client.pending(sid)
    orphan_refs = []
    original_put = h.engine.history.put

    async def remember(**values):
        ref = await original_put(**values)
        if values["message"].role == "tool":
            orphan_refs.append(ref)
        return ref

    monkeypatch.setattr(h.engine.history, "put", remember)
    async with h.store.turn(sid, h.actor.id, turn_id="failed-result") as log:
        original_append = log.append

        async def reject_result(entries):
            if any(e.key == "result:" + pending.invocation_id for e in entries):
                raise StoreError("fabricated_result_commit_failure")
            return await original_append(entries)

        monkeypatch.setattr(log, "append", reject_result)
        with pytest.raises(StoreError, match="fabricated_result_commit_failure"):
            await h.engine.answer(h.actor, log, pending.invocation_id, Answer(approved=True))
    assert len(h.tool.calls) == 1
    assert len(orphan_refs) == 1
    async with h.store.turn(sid, h.actor.id, turn_id="new-dialogue") as log:
        result = await h.engine.run_turn(
            h.actor, log, "Fabricated next dialogue", spec=h.engine.spec("demo")
        )
    assert result.stop_reason == "final"
    assert len(h.tool.calls) == 1
    closure = (
        await h.store.lookup(sid, h.actor.id, kind="key", value="result:" + pending.invocation_id)
    )[0]
    assert closure.facts["reason"] == "outcome_unknown"
    assert closure.history_ref != orphan_refs[0]
    assert len((await h.client.effects(sid)).items) == 1
    orphan = (await h.history.read((orphan_refs[0],), actor=h.actor))[0]
    assert orphan.status == "ready"
    assert orphan.record.message.content == "Wrote 3"


async def test_known_acceptance_is_signaled_before_fallible_run_setup(harness):
    from pydantic import ValidationError

    h, sid = harness, harness.session.id
    previous = await h.client.pending(sid)
    h.engine.max_output_tokens = 0
    signaled = []
    async with h.store.turn(sid, h.actor.id, turn_id="accepted-setup-failure") as log:
        with pytest.raises(ValidationError):
            await h.engine.run_turn(
                h.actor,
                log,
                "Fabricated accepted input",
                spec=h.engine.spec("demo"),
                start=TurnStart(turn_id="accepted-setup-failure", request_id="setup-failure"),
                accepted=lambda: signaled.append(True),
            )
    assert signaled == [True]
    assert await h.store.lookup(sid, h.actor.id, kind="key", value="request:setup-failure")
    from tether.types.execution import Answer

    async with h.store.turn(sid, h.actor.id, turn_id="late-confirmation") as log:
        assert (
            await h.engine.answer(h.actor, log, previous.invocation_id, Answer(approved=True))
            is None
        )
    assert len(h.model.requests) == 1
    assert not h.tool.calls


async def test_supplied_turn_identity_must_match_lease(harness):
    h, sid = harness, harness.session.id
    async with h.store.turn(sid, h.actor.id, turn_id="lease-bound") as log:
        with pytest.raises(ExecutionDenied, match="turn_identity_mismatch"):
            await h.engine.run_turn(
                h.actor,
                log,
                "Fabricated mismatch",
                spec=h.engine.spec("demo"),
                start=TurnStart(turn_id="different-id"),
            )
    assert len(h.model.requests) == 1
    assert (await h.client.pending(sid)) is not None
