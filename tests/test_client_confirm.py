"""Public confirmation owns continuation and rechecks journal, caller and disclosure authority."""

from __future__ import annotations

import asyncio

import pytest
from test_client_reads import harness as harness
from test_client_turns import WaitingModel

from tether.types.execution import Actor, Answer, Decision, ExecutionDenied
from tether.types.storage import UnknownSession

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def wait_on_continuation(h):
    h.engine._runs.clear()
    model = WaitingModel()
    h.engine.model = model
    return model


async def test_confirm_returns_after_known_acceptance_and_owns_continuation(harness):
    h = harness
    before = await h.client.snapshot(h.session.id)
    pending = await h.client.pending(h.session.id)
    model = wait_on_continuation(h)
    handle = await h.client.confirm(h.session.id, pending.invocation_id, Answer(approved=True))
    assert handle is not None
    async with asyncio.timeout(5):
        await model.entered.wait()
    running = await h.client.snapshot(h.session.id)
    assert running.view.summary.status == "running"
    assert running.view.summary.active_turn_id == handle.turn_id
    assert running.view.summary.turn_count == before.view.summary.turn_count
    assert not running.view.summary.pending
    waiting = asyncio.create_task(handle.result())
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert (await h.client.get(h.session.id)).summary.status == "running"
    model.release.set()
    assert (await handle.result()).stop_reason == "final"
    assert len(h.tool.calls) == 1
    after = await h.client.snapshot(h.session.id)
    assert (
        await h.client.confirm(h.session.id, pending.invocation_id, Answer(approved=False)) is None
    )
    assert await h.client.snapshot(h.session.id) == after


async def test_concurrent_answers_have_one_winner_without_cancelling_it(harness):
    h = harness
    pending = await h.client.pending(h.session.id)
    model = wait_on_continuation(h)
    answers = await asyncio.gather(
        *(
            h.client.confirm(h.session.id, pending.invocation_id, Answer(approved=True))
            for _ in range(2)
        )
    )
    winners = [handle for handle in answers if handle is not None]
    assert len(winners) == 1
    assert answers.count(None) == 1
    model.release.set()
    assert (await winners[0].result()).stop_reason == "final"
    assert len(h.tool.calls) == 1
    entries = await h.store.lookup(
        h.session.id, h.actor.id, kind="invocation", value=pending.invocation_id
    )
    assert sum(e.key == "approval:" + pending.invocation_id for e in entries) == 1


async def test_declined_answer_closes_call_without_dispatch(harness):
    h = harness
    pending = await h.client.pending(h.session.id)
    handle = await h.client.confirm(h.session.id, pending.invocation_id, Answer(approved=False))
    assert handle is not None
    result = await handle.result()
    assert result.stop_reason == "final"
    assert not h.tool.calls
    assert any(m.role == "tool" and m.is_error for m in result.messages)
    assert await h.client.pending(h.session.id) is None


async def test_cached_continuation_uses_fresh_caller_principal_and_credential(harness, monkeypatch):
    h = harness
    pending = await h.client.pending(h.session.id)

    class SourceCredential:
        async def authorization(self):
            return None

    credential = SourceCredential()
    actor = Actor(id=h.actor.id, principal="current-verified-principal", credential=credential)
    original, observed = h.model.stream, []

    async def capture(request):
        observed.append((request.tags.principal, request.credential is credential))
        async for item in original(request):
            yield item

    monkeypatch.setattr(h.model, "stream", capture)
    handle = await h.app.client(actor).confirm(
        h.session.id, pending.invocation_id, Answer(approved=True)
    )
    assert handle is not None
    assert (await handle.result()).stop_reason == "final"
    assert observed == [(actor.principal, True)]
    assert len(h.tool.calls) == 1


async def test_confirmation_owner_and_current_approval_policy_are_required(harness, monkeypatch):
    h = harness
    pending = await h.client.pending(h.session.id)
    with pytest.raises(UnknownSession):
        await h.app.client(Actor(id="other")).confirm(
            h.session.id, pending.invocation_id, Answer(approved=True)
        )
    original = h.policy.authorize

    async def deny_approval(request):
        if request.action == "approve":
            return Decision(allowed=False, reason="denied")
        return await original(request)

    monkeypatch.setattr(h.policy, "authorize", deny_approval)
    with pytest.raises(ExecutionDenied):
        await h.client.confirm(h.session.id, pending.invocation_id, Answer(approved=True))
    assert (await h.client.pending(h.session.id)).invocation_id == pending.invocation_id
    assert not h.tool.calls


async def test_late_none_still_rechecks_read_after_metadata_io(harness, monkeypatch):
    h = harness
    original = h.store.get

    async def revoked(*args):
        value = await original(*args)
        h.policy.read = False
        return value

    monkeypatch.setattr(h.store, "get", revoked)
    with pytest.raises(ExecutionDenied):
        await h.client.confirm(h.session.id, "no-such-invocation", Answer(approved=True))
    h.policy.read = True
    assert not h.tool.calls


async def test_retained_send_handle_waits_for_current_accepted_continuation(harness):
    from test_gated_execution import final, proposal

    from tether.models.testing import ScriptedModel
    from tether.sessions.outcomes import outcome
    from tether.types.storage import TurnInProgress

    h = harness
    h.engine.model = ScriptedModel([proposal(), final()])
    sent = await h.client.send(h.session.id, "New fabricated pending turn")
    waiting = await sent.result()
    assert waiting.stop_reason == "confirmation_required"
    pending = waiting.pending[0]
    model = wait_on_continuation(h)
    continued = await h.client.confirm(h.session.id, pending.invocation_id, Answer(approved=True))
    assert continued is not None
    async with asyncio.timeout(5):
        await model.entered.wait()
    with pytest.raises(TurnInProgress):
        await outcome(h.app.access, h.engine, h.actor, h.session.id, sent.turn_id)
    retained = asyncio.create_task(sent.result())
    try:
        await asyncio.sleep(0)
        assert not retained.done()
        model.release.set()
        result = await retained
        assert result.stop_reason == "final"
        assert result == await continued.result()
        assert len(h.tool.calls) == 1
    finally:
        model.release.set()
        await asyncio.gather(retained, return_exceptions=True)
