"""Revocation/cancellation cannot cross the new pre-model and retry boundaries."""

import asyncio

import pytest
from test_consumer_auxiliary import CurrentCredential
from test_consumer_commands import PausedPreparation, composition
from test_gated_execution import DemoTool, final

from tether.models.testing import ScriptedModel
from tether.types.auxiliary import AuxiliaryProfile
from tether.types.canonical import digest
from tether.types.execution import Actor

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_cancelled_preparation_keeps_model_egress_at_zero(tmp_path):
    preparation = PausedPreparation()
    async with composition(
        tmp_path, DemoTool(), model=ScriptedModel([final()]), prepare=preparation
    ) as (client, _app, model):
        session = await client.create()
        handle = await client.send(session.id, "Fabricated", request_id="prepare")
        await preparation.entered.wait()
        await handle.cancel()
        with pytest.raises(asyncio.CancelledError):
            await handle.result()
        preparation.release.set()
        assert not model.requests
        assert (await client.get(session.id)).summary.status == "idle"


class RetryAfterRevocation(ScriptedModel):
    retry = False
    egress = 0

    def __init__(self, credential):
        super().__init__([final()])
        self.credential = credential

    async def stream(self, request):
        if not self.retry:
            async for item in super().stream(request):
                yield item
            return
        assert request.attempts is not None
        wire = request.model_dump(mode="json")
        first = await request.attempts.reserve()
        await request.attempts.start(first, request_digest=digest(wire), wire_request=wire)
        self.egress += 1
        self.credential.available = False
        second = await request.attempts.reserve()
        await request.attempts.start(second, request_digest=digest(wire), wire_request=wire)
        self.egress += 1
        raise AssertionError("revoked credential reached a second egress")


async def test_auxiliary_refreshes_credential_between_physical_attempts(tmp_path):
    credential = CurrentCredential()
    model = RetryAfterRevocation(credential)
    profile = AuxiliaryProfile(
        id="suggestion", revision="1", model="fixture", instructions="Fabricated"
    )
    async with composition(tmp_path, DemoTool(), model=model, profiles=(profile,)) as (
        _client,
        app,
        _model,
    ):
        client = app.client(Actor(id="owner", credential=credential))
        session = await client.create()
        await (await client.send(session.id, "Fabricated")).result()
        model.retry = True
        snapshot = await client.snapshot(session.id)
        result = await (
            await client.auxiliary(
                session.id, profile.id, through_index=snapshot.through_index, request_id="retry"
            )
        ).result()
        assert result.stop_reason == "model_denied"
        assert result.reason == "missing_credential"
        assert model.egress == 1
        assert result.messages == ()
