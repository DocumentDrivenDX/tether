"""Manual resolution preserves unknown evidence and never executes another action."""

import asyncio

import pytest
from test_client_reads import harness as harness

from tether.types import Message
from tether.types.execution import (
    Actor,
    Answer,
    Decision,
    ExecutionDenied,
    NewEntry,
    NotUnresolved,
    ResolutionConflict,
)
from tether.types.storage import TurnInProgress, UnknownSession

pytestmark = pytest.mark.anyio


async def unknown(h):
    pending = await h.client.pending(h.session.id)
    h.tool.failure = RuntimeError("fabricated_effect_failure")
    handle = await h.client.confirm(h.session.id, pending.invocation_id, Answer(approved=True))
    await handle.result()
    assert (await h.client.get(h.session.id)).summary.unresolved_effect_count == 1
    return pending.invocation_id


@pytest.mark.parametrize("outcome", ["completed", "not_completed"])
async def test_resolve_clears_barrier_once_without_rewriting_evidence(harness, outcome):
    h, sid = harness, harness.session.id
    inv = await unknown(h)
    before = await h.client.snapshot(sid)
    original = (await h.client.entries(sid)).items
    counts = len(h.tool.calls), len(h.model.requests)
    resolved = await h.client.resolve(sid, inv, outcome, reason="fabricated_operator_decision")
    assert resolved.unresolved_effect_count == 0
    assert not (await h.client.effects(sid)).items
    assert resolved.turn_count == before.view.summary.turn_count
    entries = (await h.client.entries(sid)).items
    assert entries[: len(original)] == original
    assert sum(e.key == "result:" + inv for e in entries) == 1
    assert sum(e.key == "resolution:" + inv for e in entries) == 1
    assert any(
        e.kind == "authorization"
        and e.facts.get("action") == "resolve_effect"
        and e.facts["allowed"]
        for e in entries
    )
    assert (
        await h.client.resolve(sid, inv, outcome, reason="fabricated_operator_decision") == resolved
    )
    assert (len(h.tool.calls), len(h.model.requests)) == counts
    closed = await h.client.close(sid)
    assert (
        await h.client.resolve(sid, inv, outcome, reason="fabricated_operator_decision") == closed
    )


@pytest.mark.parametrize("change", ["outcome", "reason"])
async def test_conflicting_resolution_never_replaces_original(harness, change):
    h, sid = harness, harness.session.id
    inv = await unknown(h)
    resolved = await h.client.resolve(sid, inv, "completed", reason="first")
    with pytest.raises(ResolutionConflict):
        await h.client.resolve(
            sid,
            inv,
            "not_completed" if change == "outcome" else "completed",
            reason="second" if change == "reason" else "first",
        )
    assert (await h.client.get(sid)).summary == resolved


async def test_resolve_requires_current_policy_even_for_retry(harness, monkeypatch):
    h, sid = harness, harness.session.id
    inv = await unknown(h)
    original = h.policy.authorize

    async def deny(request):
        return (
            Decision(allowed=False, reason="denied")
            if request.action == "resolve_effect"
            else await original(request)
        )

    monkeypatch.setattr(h.policy, "authorize", deny)
    with pytest.raises(ExecutionDenied):
        await h.client.resolve(sid, inv, "completed", reason="decision")
    assert (await h.client.get(sid)).summary.unresolved_effect_count == 1
    monkeypatch.setattr(h.policy, "authorize", original)
    await h.client.resolve(sid, inv, "completed", reason="decision")
    monkeypatch.setattr(h.policy, "authorize", deny)
    with pytest.raises(ExecutionDenied):
        await h.client.resolve(sid, inv, "completed", reason="decision")


async def test_resolve_owner_and_unknown_target_refusals_do_not_execute(harness):
    h, sid = harness, harness.session.id
    before = await h.client.snapshot(sid)
    with pytest.raises(UnknownSession):
        await h.app.client(Actor(id="other")).resolve(
            sid, "missing", "completed", reason="decision"
        )
    with pytest.raises(NotUnresolved):
        await h.client.resolve(sid, "missing", "completed", reason="decision")
    assert await h.client.snapshot(sid) == before
    assert not h.tool.calls


async def test_resolve_intent_without_result_commits_single_unknown_closure(harness, monkeypatch):
    h, sid = harness, harness.session.id
    pending = await h.client.pending(sid)
    inv = pending.invocation_id
    original = h.store.append

    async def reject_result(session_id, owner, generation, entries):
        if any(e.key == "result:" + inv for e in entries):
            raise RuntimeError("fabricated_result_commit_failure")
        return await original(session_id, owner, generation, entries)

    monkeypatch.setattr(h.store, "append", reject_result)
    handle = await h.client.confirm(sid, inv, Answer(approved=True))
    with pytest.raises(RuntimeError, match="fabricated_result_commit_failure"):
        await handle.result()
    monkeypatch.setattr(h.store, "append", original)
    before = (await h.client.entries(sid)).items
    assert not any(e.key == "result:" + inv for e in before)
    calls = len(h.tool.calls), len(h.model.requests)
    resolved = await h.client.resolve(sid, inv, "completed", reason="operator_checked")
    assert resolved.unresolved_effect_count == 0
    entries = (await h.client.entries(sid)).items
    assert sum(e.key == "result:" + inv for e in entries) == 1
    assert sum(e.key == "message:result:" + inv for e in entries) == 1
    result = next(e for e in entries if e.key == "result:" + inv)
    assert result.facts["reason"] == "outcome_unknown"
    assert result.facts["status"] == "interrupted"
    assert (len(h.tool.calls), len(h.model.requests)) == calls


async def test_concurrent_resolution_has_one_record_and_conflict_refuses(harness):
    h, sid = harness, harness.session.id
    inv = await unknown(h)
    results = await asyncio.gather(
        h.client.resolve(sid, inv, "completed", reason="same"),
        h.client.resolve(sid, inv, "completed", reason="same"),
        return_exceptions=True,
    )
    assert any(not isinstance(r, BaseException) for r in results)
    assert all(not isinstance(r, BaseException) or isinstance(r, TurnInProgress) for r in results)
    await h.client.resolve(sid, inv, "completed", reason="same")
    assert sum(e.key == "resolution:" + inv for e in (await h.client.entries(sid)).items) == 1


async def test_resolution_content_failure_and_revocation_leave_barrier(harness, monkeypatch):
    h, sid = harness, harness.session.id
    pending = await h.client.pending(sid)
    inv = pending.invocation_id
    original_append = h.store.append

    async def reject_result(session_id, owner, generation, entries):
        if any(e.key == "result:" + inv for e in entries):
            raise RuntimeError("fabricated_commit_failure")
        return await original_append(session_id, owner, generation, entries)

    monkeypatch.setattr(h.store, "append", reject_result)
    handle = await h.client.confirm(sid, inv, Answer(approved=True))
    with pytest.raises(RuntimeError):
        await handle.result()
    monkeypatch.setattr(h.store, "append", original_append)
    before = await h.client.snapshot(sid)
    original_put = h.engine.history.put

    async def failure(**kwargs):
        raise RuntimeError("fabricated_history_failure")

    monkeypatch.setattr(h.engine.history, "put", failure)
    with pytest.raises(RuntimeError, match="fabricated_history_failure"):
        await h.client.resolve(sid, inv, "completed", reason="decision")
    assert await h.client.snapshot(sid) == before

    async def revoke(**kwargs):
        ref = await original_put(**kwargs)
        h.policy.read = False
        return ref

    monkeypatch.setattr(h.engine.history, "put", revoke)
    with pytest.raises(ExecutionDenied):
        await h.client.resolve(sid, inv, "completed", reason="decision")
    h.policy.read = True
    assert (await h.client.get(sid)).summary.unresolved_effect_count == 1
    assert not any(e.key == "resolution:" + inv for e in (await h.client.entries(sid)).items)


async def interrupted_intent(h, monkeypatch):
    pending = await h.client.pending(h.session.id)
    original = h.store.append

    async def reject_result(session_id, owner, generation, entries):
        if any(e.key == "result:" + pending.invocation_id for e in entries):
            raise RuntimeError("fabricated_commit_failure")
        return await original(session_id, owner, generation, entries)

    monkeypatch.setattr(h.store, "append", reject_result)
    handle = await h.client.confirm(h.session.id, pending.invocation_id, Answer(approved=True))
    with pytest.raises(RuntimeError):
        await handle.result()
    monkeypatch.setattr(h.store, "append", original)
    entries = (await h.client.entries(h.session.id)).items
    return next(e for e in entries if e.key == "intent:" + pending.invocation_id)


async def partial_record(h, intent, variant, malformed=None):
    inv = intent.facts["invocation_id"]
    call_id = intent.facts["call_id"]
    ref = await h.engine.history.put(
        actor=h.actor,
        session_id=h.session.id,
        key="fabricated-partial:" + inv,
        message=Message(
            role="tool",
            content="outcome_unknown",
            is_error=True,
            tool_call_id="wrong" if malformed == "content_call" else call_id,
        ),
    )
    if malformed in {"owner", "kind"}:
        ref = ref.model_copy(
            update={"owner": "other"} if malformed == "owner" else {"kind": "summary"}
        )
    facts = (
        {
            "role": "tool",
            "tool_call_id": "wrong" if malformed == "call_id" else call_id,
            "is_error": True,
        }
        if variant == "message"
        else {
            "invocation_id": inv,
            "tool": intent.facts["tool"],
            "status": "interrupted",
            "reason": "outcome_unknown",
            "effect": "effectful",
            "replay": "unsafe",
        }
    )
    record = NewEntry(
        kind="message" if variant == "message" else "tool_call",
        key=("message:result:" if variant == "message" else "result:") + inv,
        turn_id=intent.turn_id,
        history_ref=None if malformed == "no_ref" else ref,
        facts=facts,
    )
    async with h.store.turn(h.session.id, h.actor.id, turn_id="partial-fixture") as log:
        await log.append([record])
    return record


@pytest.mark.parametrize("variant", ["message", "result"])
async def test_valid_partial_unknown_group_is_completed_without_duplicate_message(
    harness, monkeypatch, variant
):
    h, sid = harness, harness.session.id
    intent = await interrupted_intent(h, monkeypatch)
    inv = intent.facts["invocation_id"]
    partial = await partial_record(h, intent, variant)
    calls = len(h.tool.calls), len(h.model.requests)
    result = await h.client.resolve(sid, inv, "completed", reason="operator_checked")
    assert result.unresolved_effect_count == 0
    entries = (await h.client.entries(sid)).items
    message = next(e for e in entries if e.key == "message:result:" + inv)
    terminal = next(e for e in entries if e.key == "result:" + inv)
    assert message.history_ref == terminal.history_ref == partial.history_ref
    assert sum(e.key == message.key for e in entries) == 1
    assert sum(e.key == terminal.key for e in entries) == 1
    assert (len(h.tool.calls), len(h.model.requests)) == calls


@pytest.mark.parametrize("malformed", ["no_ref", "owner", "kind", "call_id", "content_call"])
async def test_malformed_partial_message_cannot_clear_unknown_barrier(
    harness, monkeypatch, malformed
):
    h, sid = harness, harness.session.id
    intent = await interrupted_intent(h, monkeypatch)
    inv = intent.facts["invocation_id"]
    await partial_record(h, intent, "message", malformed)
    calls = len(h.tool.calls), len(h.model.requests)
    with pytest.raises(ExecutionDenied, match="resolution_history_integrity"):
        await h.client.resolve(sid, inv, "completed", reason="operator_checked")
    assert (await h.client.get(sid)).summary.unresolved_effect_count == 1
    assert not any(e.key == "resolution:" + inv for e in (await h.client.entries(sid)).items)
    assert (len(h.tool.calls), len(h.model.requests)) == calls
