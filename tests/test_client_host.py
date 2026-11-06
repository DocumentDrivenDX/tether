"""Public host construction and journal-backed client behavior."""

from __future__ import annotations

import pytest

from tether.client import ClientHost, ClientSettings, LocalJsonlSettings
from tether.context.testing import fabricated_context
from tether.engine.governance import AllowAll
from tether.models.testing import ScriptedModel
from tether.types import ModelResponse
from tether.types.client import UnknownTurn
from tether.types.execution import Actor, AgentSpec
from tether.types.storage import SessionInit

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def settings(path, *, model, client_limits, model_limits):
    context = fabricated_context()
    return ClientSettings(
        model=model,
        storage=LocalJsonlSettings(path),
        policy=AllowAll(),
        client_limits=client_limits,
        model_limits=model_limits,
        context_config=context["context_config"],
        estimator=context["estimator"],
        specs=(
            AgentSpec(
                id="demo",
                version="1",
                title="Fabricated",
                instructions="Respond with fabricated content.",
                model="demo",
            ),
        ),
        default_spec="demo",
    )


async def test_host_opens_owned_jsonl_and_turn_result_survives_host_restart(
    tmp_path, client_limits, limits
):
    actor = Actor(id="owner")
    first_settings = settings(
        tmp_path,
        model=ScriptedModel([ModelResponse(content="Fabricated answer", finish_reason="stop")]),
        client_limits=client_limits,
        model_limits=limits,
    )
    async with await ClientHost.open(first_settings) as host:
        client = host.client(actor)
        session = await client.create(spec="demo")
        handle = await client.send(session.id, "Fabricated request", spec="demo")
        result = await handle.result()
        assert result.stop_reason == "final"
        assert await client.turn_result(session.id, handle.turn_id) == result
        with pytest.raises(UnknownTurn, match="unknown_turn"):
            await client.turn_result(session.id, "unknown-turn")

    reopened_settings = settings(
        tmp_path,
        model=ScriptedModel([]),
        client_limits=client_limits,
        model_limits=limits,
    )
    async with await ClientHost.open(reopened_settings) as reopened:
        recovered = await reopened.client(actor).turn_result(session.id, handle.turn_id)
        assert recovered == result


async def test_host_close_does_not_close_borrowed_storage(tmp_path, client_limits, limits):
    from tether.client import ProvidedStorage
    from tether.history.jsonl import JsonlHistory
    from tether.stores.jsonl import JsonlRoot, JsonlSessionStore

    root = await JsonlRoot.open(tmp_path)
    store = await JsonlSessionStore.open(root)
    history = await JsonlHistory.open(root)
    context = fabricated_context()
    config = ClientSettings(
        model=ScriptedModel([]),
        storage=ProvidedStorage(store=store, history=history),
        policy=AllowAll(),
        client_limits=client_limits,
        model_limits=limits,
        context_config=context["context_config"],
        estimator=context["estimator"],
        specs=(),
        resume="off",
    )
    host = await ClientHost.open(config)
    assert await host.aclose()
    actor = Actor(id="owner")
    session = await store.create(actor.id, SessionInit())
    assert await store.get(session.id, actor.id) is not None
    assert await store.aclose()
