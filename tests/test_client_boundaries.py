"""Client prerequisites keep model attribution separate from ownership and disclosure."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tether.engine import Engine
from tether.engine.governance import AllowAll
from tether.history.jsonl import JsonlHistory
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ScriptedModel
from tether.sessions.archive import ArchiveTransfer
from tether.stores.jsonl import JsonlSessionStore
from tether.stores.memory import MemoryFiles
from tether.types import ModelResponse
from tether.types.execution import (
    Actor,
    AgentSpec,
    AuthzRequest,
    Decision,
    ExecutionDenied,
    NewEntry,
)
from tether.types.history import HistoryRecord
from tether.types.storage import SessionInit

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class MetadataOnly(AllowAll):
    def __init__(self):
        self.requests = []

    async def authorize(self, request: AuthzRequest) -> Decision:
        self.requests.append(request)
        return Decision(allowed=request.action != "read_call_payload", reason="denied")


@pytest.mark.parametrize("principal", [None, "model-subject"])
async def test_model_principal_does_not_change_owner_or_policy_subject(principal, limits):
    files = MemoryFiles()
    store = await JsonlSessionStore.open(files)
    history = await JsonlHistory.open(files)
    actor = Actor(id="session-owner", principal=principal)
    session = await store.create(actor.id, SessionInit())
    model = ScriptedModel([ModelResponse(content="Fabricated answer", finish_reason="stop")])
    policy = MetadataOnly()
    engine = Engine(
        model=model,
        history=MessageHistoryAdapter(history),
        history_provider=history,
        model_limits=limits,
        policy=policy,
    )
    spec = AgentSpec(id="demo", version="1", title="Demo", model="demo", instructions="Fabricated")
    async with store.turn(session.id, actor.id, turn_id="turn") as log:
        result = await engine.run_turn(actor, log, "Fabricated", spec=spec)
    assert result.stop_reason == "final"
    assert model.requests[0].tags.principal == (principal or actor.id)
    assert all(request.actor.id == actor.id for request in policy.requests)
    assert (await store.snapshot(session.id, actor.id)).view.summary.owner == actor.id
    assert await store.get(session.id, principal or "other") is None
    assert await engine.aclose()


async def test_empty_principal_is_not_silently_replaced():
    with pytest.raises(ValidationError):
        Actor(id="owner", principal="")


async def test_raw_exchange_export_needs_current_payload_permission():
    files = MemoryFiles()
    store = await JsonlSessionStore.open(files)
    history = await JsonlHistory.open(files)
    actor = Actor(id="owner")
    session = await store.create(actor.id, SessionInit())
    ref = await history.put(
        actor=actor,
        session_id=session.id,
        key="wire",
        record=HistoryRecord(
            kind="model_exchange",
            request={"fabricated": "request"},
            response={"fabricated": "response"},
        ),
    )
    async with store.turn(session.id, actor.id, turn_id="turn") as log:
        await log.append(
            [NewEntry(kind="model_call", facts={"exchange_ref": ref.model_dump(mode="json")})]
        )
    policy = MetadataOnly()
    assert (await store.snapshot(session.id, actor.id)).view.summary.entry_count == 1
    with pytest.raises(ExecutionDenied, match="archive_denied"):
        await ArchiveTransfer(store, history, policy).export(session.id, actor)
    assert policy.requests[-1].action == "read_call_payload"
    bundle = await ArchiveTransfer(store, history, AllowAll()).export(session.id, actor)
    assert len(bundle.records) == 1


async def test_wrong_loop_refuses_before_closing_or_disclosing_resources(tmp_path, limits):
    import asyncio

    from tether.stores.jsonl import JsonlRoot
    from tether.types.storage import RootBusy

    actor = Actor(id="owner")
    root = await JsonlRoot.open(tmp_path)
    try:
        store = await JsonlSessionStore.open(root)
        provider = await JsonlHistory.open(root)
        session = await store.create(actor.id, SessionInit())
        engine = Engine(
            model=ScriptedModel([ModelResponse(content="Fabricated", finish_reason="stop")]),
            history=MessageHistoryAdapter(provider),
            model_limits=limits,
        )
        spec = AgentSpec(id="demo", version="1", title="Demo", model="demo", instructions="Test")
        async with store.turn(session.id, actor.id, turn_id="turn") as log:
            await engine.run_turn(actor, log, "Fabricated", spec=spec)

        async def wrong_loop():
            with pytest.raises(RuntimeError, match="wrong_event_loop"):
                await store.get(session.id, actor.id)
            with pytest.raises(RuntimeError, match="wrong_event_loop"):
                await engine.aclose()
            with pytest.raises(RuntimeError, match="wrong_event_loop"):
                await root.aclose()

        await asyncio.to_thread(asyncio.run, wrong_loop())
        assert await store.get(session.id, actor.id) is not None
        with pytest.raises(RootBusy):
            await JsonlRoot.open(tmp_path)
        assert await engine.aclose()
    finally:
        assert await root.aclose()
