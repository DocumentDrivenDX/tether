"""Cancel during engine-owned persistence settles writes before turn ownership exits."""

from __future__ import annotations

import asyncio

import pytest
from selected_profiles_process import await_retained

from tether.cli import demo
from tether.client import ModelResponse, TextDelta
from tether.client.testing import scripted_model
from tether.engine.persistence import settled
from tether.stores.jsonl.root import JsonlRoot
from tether.types.execution import CommitUnknown

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def event_or_operation_terminal(event: asyncio.Event, operation: asyncio.Future):
    ready = asyncio.create_task(event.wait())
    try:
        done, _ = await asyncio.wait({ready, operation}, return_when=asyncio.FIRST_COMPLETED)
        if operation in done and ready not in done:
            await operation
            raise AssertionError("operation terminated before event witness")
        await ready
    finally:
        if not ready.done():
            ready.cancel()
        await _settle_tasks(ready)


async def _settle_tasks(*tasks: asyncio.Future):
    async def settle() -> None:
        await asyncio.gather(*tasks, return_exceptions=True)

    await await_retained(asyncio.create_task(settle()))


class HeldModel:
    provider = "fabricated-cancel-test"

    def __init__(self, started, release):
        self.started, self.release = started, release

    async def stream(self, request):
        model = scripted_model(
            (
                TextDelta(text="Fabricated provisional text"),
                ModelResponse(content="Fabricated complete", finish_reason="stop"),
            )
        )
        async for item in model.stream(request):
            self.started.set()
            await self.release.wait()
            yield item
            await asyncio.Event().wait()


@pytest.mark.parametrize("phase", ["disclosure-journal", "exchange-history"])
async def test_cancelled_turn_settles_admitted_write_and_keeps_jsonl_usable(
    tmp_path, monkeypatch, phase
):
    blocked, release, model_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    allow_delta, cancel_ready, allow_cancel = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_policy = demo.demo_policy()

    class Policy:
        calls = 0

        async def authorize(self, request):
            task = asyncio.current_task()
            if task is not None and task.get_name() == "fabricated-canceller":
                self.calls += 1
                if self.calls == 2:
                    cancel_ready.set()
                    await allow_cancel.wait()
            return await original_policy.authorize(request)

    original = JsonlRoot.append_file

    async def append(files, name, value, *, guard=None):
        journal = (
            name == "journal"
            and model_started.is_set()
            and any(entry["kind"] == "authorization" for entry in value.get("entries", ()))
        )
        history = name == "history" and value["record"]["kind"] == "model_exchange"
        target = journal if phase == "disclosure-journal" else history
        if target and not blocked.is_set():
            blocked.set()
            await release.wait()
        return await original(files, name, value, guard=guard)

    monkeypatch.setattr(JsonlRoot, "append_file", append)
    monkeypatch.setattr(demo, "demo_model", lambda **_: HeldModel(model_started, allow_delta))
    monkeypatch.setattr(demo, "demo_policy", Policy)
    async with demo.open_demo(tmp_path) as (host, actor, _):
        client = host.client(actor)
        session = await client.create(ref="demo")
        turn = await client.send(session.id, "Fabricated cancel request")
        await _drive_cancelled_turn(
            client,
            host,
            session.id,
            turn,
            phase,
            blocked,
            release,
            model_started,
            allow_delta,
            cancel_ready,
            allow_cancel,
        )


async def _drive_cancelled_turn(
    client,
    host,
    session_id,
    turn,
    phase,
    blocked,
    release,
    model_started,
    allow_delta,
    cancel_ready,
    allow_cancel,
):
    outcome = asyncio.create_task(turn.result())
    cancelling: asyncio.Task[bool] | None = None
    try:
        readiness = model_started if phase == "disclosure-journal" else blocked
        await event_or_operation_terminal(readiness, outcome)
        cancelling = asyncio.create_task(
            client.cancel_turn(session_id, turn.turn_id), name="fabricated-canceller"
        )
        await event_or_operation_terminal(cancel_ready, cancelling)
        if phase == "disclosure-journal":
            allow_delta.set()
            await event_or_operation_terminal(blocked, outcome)
        allow_cancel.set()
        assert await cancelling
        # Cancellation must retain the admitted write and the turn's physical owner.
        assert not host.drained
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await outcome
        await assert_healthy(client, session_id, turn.turn_id)
    finally:
        release.set()
        allow_delta.set()
        allow_cancel.set()
        pending = [outcome]
        if cancelling is not None:
            pending.append(cancelling)
        await _settle_tasks(*pending)


async def assert_healthy(client, session_id, turn_id):
    assert (await client.get(session_id)).summary.status == "idle"
    assert (await client.snapshot(session_id)).view.summary.status == "idle"
    result = await client.turn_result(session_id, turn_id)
    assert result is not None
    assert result.stop_reason == "cancelled"
    assert (await client.create()).id != session_id


async def test_repeated_cancellation_cannot_release_unsettled_persistence():
    entered, release = asyncio.Event(), asyncio.Event()

    async def write():
        entered.set()
        await release.wait()
        return "accepted"

    task = asyncio.create_task(settled(write()))
    try:
        await event_or_operation_terminal(entered, task)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await _settle_tasks(task)


async def test_real_commit_uncertainty_remains_authoritative_after_cancellation():
    entered, release = asyncio.Event(), asyncio.Event()

    async def write():
        entered.set()
        await release.wait()
        raise CommitUnknown("fabricated_commit_unknown")

    task = asyncio.create_task(settled(write()))
    try:
        await event_or_operation_terminal(entered, task)
        task.cancel()
        release.set()
        with pytest.raises(CommitUnknown, match="fabricated_commit_unknown"):
            await task
    finally:
        release.set()
        await _settle_tasks(task)
