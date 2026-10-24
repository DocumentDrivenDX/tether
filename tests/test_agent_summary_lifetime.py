"""Summary deadlines and cancellation retain actual model work and session ownership."""

import asyncio
from contextlib import suppress

import pytest
from test_agent_summaries import configuration, fixture, response, turn

from tether.models.testing import ScriptedModel
from tether.types.execution import WorkerOutstanding

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("times_out", [False, True])
async def test_abandoned_summary_retains_actual_model_work_and_session(limits, times_out):
    entered, release = asyncio.Event(), asyncio.Event()

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
        try:
            await asyncio.wait_for(entered.wait(), 1)
            if times_out:
                result, _ = await asyncio.wait_for(task, 1)
                assert (result.stop_reason, result.reason) == (
                    "context_unavailable",
                    "context_failed",
                )
            else:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 1)
            assert h.engine.workers.busy(h.session.id)
            with pytest.raises(WorkerOutstanding):
                await turn(h, "third")
        finally:
            release.set()
            await asyncio.gather(*tuple(h.engine.workers.active), return_exceptions=True)
        assert len(model.requests) == 2
        async with h.store.turn(h.session.id, h.actor.id, turn_id="inspection") as log:
            assert not any(e.kind == "compaction" for e in log.entries)
