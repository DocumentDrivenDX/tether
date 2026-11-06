"""Specification transitions bind the new definition at a completed group boundary."""

import pytest
from test_consumer_commands import composition
from test_gated_execution import DemoTool, final, proposal

from tether.models.testing import ScriptedModel
from tether.types.canonical import digest
from tether.types.execution import AgentSpec, ExecutionDenied

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_transition_updates_snapshot_and_next_request_and_retries_without_model(tmp_path):
    model = ScriptedModel([final(), final()])
    async with composition(tmp_path, DemoTool(), model=model) as (client, app, _model):
        new = AgentSpec(
            id="next",
            version="2",
            title="Next",
            instructions="Fabricated new instructions",
            model="fixture",
            tools=(),
        )
        app.engine._specs[digest(new.model_dump(mode="json"))] = new
        session = await client.create()
        await (await client.send(session.id, "First fabricated request")).result()
        view = await client.get(session.id)
        assert view.summary.spec_id is not None
        old = view.specs[view.summary.spec_id].digest
        first = await client.transition_spec(
            session.id, "next", request_id="transition", expected_spec_digest=old
        )
        assert (await first.result()).stop_reason == "final"
        assert len(model.requests) == 1
        view = await client.get(session.id)
        assert view.summary.spec_id == "next"
        assert view.specs["next"].digest == digest(new.model_dump(mode="json"))
        duplicate = await client.transition_spec(
            session.id, "next", request_id="transition", expected_spec_digest=old
        )
        assert duplicate.turn_id == first.turn_id
        await (await client.send(session.id, "Second fabricated request")).result()
        assert model.requests[-1].system == new.instructions
        assert model.requests[-1].tools == ()
        with pytest.raises(ExecutionDenied, match="spec_transition_conflict"):
            await client.transition_spec(
                session.id, "next", request_id="stale", expected_spec_digest=old
            )


async def test_transition_refuses_unsettled_approval_and_preserves_pending(tmp_path):
    tool = DemoTool()
    tool.always_ask = True
    async with composition(tmp_path, tool, model=ScriptedModel([proposal()])) as (
        client,
        _app,
        _model,
    ):
        session = await client.create()
        pending = (await (await client.send(session.id, "Fabricated request")).result()).pending[0]
        with pytest.raises(ExecutionDenied, match="spec_transition_unsettled"):
            await client.transition_spec(session.id, "fixture", request_id="unsettled")
        current = await client.pending(session.id)
        assert current is not None
        assert current.invocation_id == pending.invocation_id
        assert not tool.calls
