"""Approval acceptance and resumed projections come from the journal, never cached runners."""

from __future__ import annotations

import asyncio

import pytest
from test_client_reads import harness as harness
from test_client_turns import WaitingModel

from tether.context.testing import fabricated_context
from tether.engine import Engine
from tether.types.execution import Answer

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def other_engine(h, model):
    return Engine(
        **fabricated_context(),
        model=model,
        history=h.engine.history,
        history_provider=h.history,
        model_limits=h.engine.model_limits,
        tools=(h.tool,),
        specs=(h.engine.spec("demo"),),
        policy=h.policy,
    )


async def test_cached_runner_cannot_answer_after_another_engine_already_won(harness):
    h = harness
    pending = await h.client.pending(h.session.id)
    second = other_engine(h, h.model)
    try:
        async with h.store.turn(h.session.id, h.actor.id, turn_id="other-answer") as log:
            result = await second.answer(h.actor, log, pending.invocation_id, Answer(approved=True))
        assert result is not None
        assert result.stop_reason == "final"
        assert len(h.tool.calls) == 1
        before = await h.client.snapshot(h.session.id)
        async with h.store.turn(h.session.id, h.actor.id, turn_id="late-answer") as log:
            assert (
                await h.engine.answer(h.actor, log, pending.invocation_id, Answer(approved=True))
                is None
            )
        assert await h.client.snapshot(h.session.id) == before
        assert len(h.tool.calls) == 1
    finally:
        assert await second.aclose()


async def test_approval_and_running_projection_are_one_known_commit(harness, monkeypatch):
    h = harness
    before = await h.client.snapshot(h.session.id)
    pending, model = await h.client.pending(h.session.id), WaitingModel()
    second, accepted = other_engine(h, model), asyncio.Event()
    batches, observed = [], []
    original = h.store._commit

    async def record(event, *args):
        await original(event, *args)
        if event.operation == "append":
            batches.append(tuple(e.kind for e in event.entries))

    monkeypatch.setattr(h.store, "_commit", record)

    async def answer():
        async with h.store.turn(h.session.id, h.actor.id, turn_id="answer") as log:

            def notify():
                observed.append(tuple(e.kind for e in log.entries[-2:]))
                accepted.set()

            return await second.answer(
                h.actor, log, pending.invocation_id, Answer(approved=True), accepted=notify
            )

    task = asyncio.create_task(answer())
    try:
        async with asyncio.timeout(5):
            await accepted.wait()
        current = await h.client.snapshot(h.session.id)
        assert observed == [("confirmation", "turn_resumed")]
        assert ("confirmation", "turn_resumed") in batches
        assert current.view.summary.status == "running"
        assert not current.view.summary.resumable
        assert current.view.summary.turn_count == before.view.summary.turn_count
        assert current.view.summary.active_turn_id is not None
        model.release.set()
        result = await task
        assert result is not None
        assert result.stop_reason == "final"
        assert len(h.tool.calls) == 1
    finally:
        model.release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert await second.aclose()


async def test_known_approval_precedes_fallible_continuation_and_late_answer_is_none(
    harness, monkeypatch
):
    h = harness
    pending = await h.client.pending(h.session.id)
    run = h.engine._runs[h.session.id]
    accepted = []

    async def failed(*args, **kwargs):
        raise RuntimeError("fabricated_continuation_failure")

    monkeypatch.setattr(run, "finish", failed)
    with pytest.raises(RuntimeError, match="fabricated_continuation_failure"):
        async with h.store.turn(h.session.id, h.actor.id, turn_id="answer") as log:
            await h.engine.answer(
                h.actor,
                log,
                pending.invocation_id,
                Answer(approved=True),
                accepted=lambda: accepted.append(True),
            )
    assert accepted == [True]
    assert (await h.client.get(h.session.id)).summary.status == "interrupted"
    assert await h.client.pending(h.session.id) is None
    assert not h.tool.calls
    async with h.store.turn(h.session.id, h.actor.id, turn_id="late") as log:
        assert (
            await h.engine.answer(h.actor, log, pending.invocation_id, Answer(approved=True))
            is None
        )
    assert not h.tool.calls
