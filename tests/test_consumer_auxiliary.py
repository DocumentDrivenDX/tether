"""Application-scheduled auxiliary work is tool-free, caller-bound and durable."""

import pytest
from test_consumer_commands import composition
from test_gated_execution import DemoTool, final

from tether.models.testing import ScriptedModel
from tether.types.auxiliary import AuxiliaryProfile
from tether.types.execution import Actor
from tether.types.usage import UsageLedger

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class CurrentCredential:
    def __init__(self):
        self.available = True
        self.calls = 0

    async def authorization(self):
        self.calls += 1
        return " ".join(("Bearer", "fabricated")) if self.available else None


async def test_tool_free_auxiliary_result_is_distinct_and_request_retry_is_durable(tmp_path):
    credential = CurrentCredential()
    model = ScriptedModel([final(), final()])
    profile = AuxiliaryProfile(
        id="suggestion",
        revision="1",
        model="fixture",
        instructions="Suggest a fabricated label.",
    )
    async with composition(tmp_path, DemoTool(), model=model, profiles=(profile,)) as (
        _client,
        app,
        _model,
    ):
        client = app.client(Actor(id="owner", credential=credential))
        session = await client.create()
        await (await client.send(session.id, "Fabricated request")).result()
        snapshot = await client.snapshot(session.id)
        handle = await client.auxiliary(
            session.id, "suggestion", through_index=snapshot.through_index, request_id="suggestion"
        )
        result = await handle.result()
        assert result.stop_reason == "final"
        assert result.messages[0].content
        request = model.requests[-1]
        assert request.tools == ()
        assert request.tags.purpose == "auxiliary"
        assert request.tags.purpose_profile == "suggestion"
        assert request.identity_mode == "caller"
        duplicate = await client.auxiliary(
            session.id, "suggestion", through_index=snapshot.through_index, request_id="suggestion"
        )
        assert duplicate.turn_id == handle.turn_id
        assert (await duplicate.result()).messages == result.messages
        assert credential.calls >= 2
        assert len(model.requests) == 2
        ledger = UsageLedger()
        for entry in (await client.entries(session.id, start=0, limit=100)).items:
            ledger.apply(entry)
        assert ledger.snapshot().missing_by_purpose["auxiliary"] > 0


async def test_missing_caller_credential_refuses_auxiliary_without_egress(tmp_path):
    model = ScriptedModel([final()])
    profile = AuxiliaryProfile(
        id="suggestion",
        revision="1",
        model="fixture",
        instructions="Suggest a fabricated label.",
    )
    async with composition(tmp_path, DemoTool(), model=model, profiles=(profile,)) as (
        client,
        _app,
        _model,
    ):
        session = await client.create()
        await (await client.send(session.id, "Fabricated request")).result()
        snapshot = await client.snapshot(session.id)
        result = await (
            await client.auxiliary(
                session.id,
                "suggestion",
                through_index=snapshot.through_index,
                request_id="suggestion",
            )
        ).result()
        assert result.stop_reason == "model_denied"
        assert result.reason == "missing_credential"
        assert len(model.requests) == 1


@pytest.mark.parametrize("identity_mode", ["anonymous", "application"])
async def test_auxiliary_remains_caller_bound_when_answer_identity_changes(tmp_path, identity_mode):
    model = ScriptedModel([final(), final()])
    profile = AuxiliaryProfile(
        id="suggestion",
        revision="1",
        model="fixture",
        instructions="Suggest a fabricated label.",
    )
    async with composition(
        tmp_path,
        DemoTool(),
        model=model,
        profiles=(profile,),
        identity_mode=identity_mode,
    ) as (client, app, _model):
        session = await client.create()
        await (await client.send(session.id, "Fabricated request")).result()
        assert model.requests[0].identity_mode == identity_mode
        snapshot = await client.snapshot(session.id)
        denied = await (
            await client.auxiliary(
                session.id,
                "suggestion",
                through_index=snapshot.through_index,
                request_id="missing-caller",
            )
        ).result()
        assert (denied.stop_reason, denied.reason) == ("model_denied", "missing_credential")
        assert len(model.requests) == 1
        credential = CurrentCredential()
        caller = app.client(Actor(id="owner", credential=credential))
        snapshot = await caller.snapshot(session.id)
        accepted = await (
            await caller.auxiliary(
                session.id,
                "suggestion",
                through_index=snapshot.through_index,
                request_id="actual-caller",
            )
        ).result()
        assert accepted.stop_reason == "final"
        assert len(model.requests) == 2
        assert model.requests[-1].identity_mode == "caller"
        assert model.requests[-1].credential is credential
