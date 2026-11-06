"""Reference composition consumes only supported public client factories."""

from dataclasses import replace

import pytest

from tether.client import (
    Actor,
    AgentSpec,
    ClientHost,
    ClientSettings,
    ModelResponse,
    Unsupported,
    VolatileSettings,
)
from tether.client.testing import demo_policy, fabricated_model_context, scripted_model

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def configuration(client_limits, limits):
    context = fabricated_model_context()
    return ClientSettings(
        model=scripted_model([ModelResponse(content="Fabricated reply", finish_reason="stop")]),
        storage=VolatileSettings(),
        policy=demo_policy(),
        client_limits=client_limits,
        model_limits=limits,
        context_config=context["context_config"],
        estimator=context["estimator"],
        specs=(
            AgentSpec(
                id="demo", version="1", title="Fabricated", instructions="Demo", model="demo"
            ),
        ),
        default_spec="demo",
        resume="off",
    )


async def test_volatile_public_host_completes_without_files(
    client_limits, limits, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    async with await ClientHost.open(configuration(client_limits, limits)) as host:
        client = host.client(Actor(id="fabricated-owner"))
        session = await client.create(spec="demo")
        result = await (await client.send(session.id, "Fabricated request", spec="demo")).result()
        assert result.messages[-1].content == "Fabricated reply"
        assert host.store.capabilities.durability == "none"
    assert list(tmp_path.iterdir()) == []
    assert host.drained


@pytest.mark.parametrize("resume", ["report", "auto"])
async def test_volatile_profile_refuses_recovery_before_admission(client_limits, limits, resume):
    settings = replace(configuration(client_limits, limits), resume=resume)
    with pytest.raises(Unsupported, match="volatile_storage_requires_resume_off"):
        await ClientHost.open(settings)
