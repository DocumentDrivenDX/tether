"""Summary deadlines and cancellation retain actual model work and session ownership."""

import asyncio
from contextlib import suppress

import pytest
from context_lifetime_controls import ContextClock, settle_tasks, wait_event_or_task
from test_agent_summaries import configuration, fixture, response, turn

import tether.engine.context.step as context_step
from tether.models.testing import ScriptedModel
from tether.types.execution import WorkerOutstanding

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("times_out", [False, True])
async def test_abandoned_summary_retains_actual_model_work_and_session(
    limits, times_out, monkeypatch
):
    entered, release = asyncio.Event(), asyncio.Event()
    clock = ContextClock()
    clock.install(context_step, monkeypatch)

    class Model(ScriptedModel):
        async def stream(self, request):
            if request.tags.purpose == "summary":
                entered.set()
                while not release.is_set():
                    with suppress(asyncio.CancelledError):
                        await release.wait()
            async for item in super().stream(request):
                yield item

    model = Model([response(), response("Late generated summary"), response()])
    values = configuration(timeout_seconds=0.2 if times_out else 10.0)
    async with fixture(limits, model=model, settings=values) as h:
        await turn(h, "first")
        task = asyncio.create_task(turn(h, "second"))
        owned_workers = ()
        try:
            await wait_event_or_task(entered, task)
            if times_out:
                context_timer = _arm_summary_deadline(
                    clock, values["context_config"].timeout_seconds
                )
                result, _ = await task
                assert context_timer.expired()
                assert (result.stop_reason, result.reason) == (
                    "context_unavailable",
                    "context_failed",
                )
            else:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert h.engine.workers.busy(h.session.id)
            with pytest.raises(WorkerOutstanding):
                await turn(h, "third")
            owned_workers = tuple(h.engine.workers.active)
        finally:
            release.set()
            await settle_tasks((task, *owned_workers, *tuple(h.engine.workers.active)))
        assert len(model.requests) == 2
        async with h.store.turn(h.session.id, h.actor.id, turn_id="inspection") as log:
            assert not any(e.kind == "compaction" for e in log.entries)


def _arm_summary_deadline(clock, expected_seconds):
    deadline = clock.deadlines[-1]
    duration = deadline - clock.now
    if duration != expected_seconds:
        raise AssertionError("summary_timeout_value_changed")
    clock.advance_past(deadline)
    loop_time = asyncio.get_running_loop().time()
    context_timer = clock.timers[-1]
    context_timer.reschedule(loop_time + duration)
    return context_timer
