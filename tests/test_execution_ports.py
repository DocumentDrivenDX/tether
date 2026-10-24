"""Conformance of the slice 2 journal/content subsets and governance defaults."""

from __future__ import annotations

import pytest
from test_gated_execution import DemoTool

from tether.engine.governance import AllowAll
from tether.engine.providers import BaseProvider, structural_subset
from tether.engine.tools import Registry
from tether.testing.execution import MemoryHistory, MemoryTurnLog
from tether.types import Message, ToolCall
from tether.types.execution import (
    Actor,
    AgentSpec,
    AuthzRequest,
    EntryConflict,
    ExecutionDenied,
    NewEntry,
    SessionState,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("factory", [MemoryTurnLog])
async def test_turn_log_subset_conformance_atomic_keys_and_snapshots(factory):
    log = factory(SessionState(id="session", owner="owner"))
    first = NewEntry(kind="tool_call", key="intent:one", facts={"status": "started"})
    accepted = await log.append([first])
    assert (await log.append([first])) == accepted
    assert accepted[0].index == 0
    accepted[0].facts["status"] = "mutated"
    assert log.entries[0].facts["status"] == "started"
    before = log.entries
    with pytest.raises(EntryConflict):
        await log.append([NewEntry(kind="one"), first.model_copy(update={"kind": "changed"})])
    assert log.entries == before
    assert log.durability == "none"


@pytest.mark.parametrize("factory", [MemoryHistory])
async def test_history_subset_conformance_ownership_integrity_and_copy(factory):
    history, actor = factory(), Actor(id="owner")
    message = Message(role="user", content="fabricated")
    ref = await history.put(actor=actor, session_id="session", key="one", message=message)
    assert ref == await history.put(actor=actor, session_id="session", key="one", message=message)
    assert await history.read(ref, actor=actor, session_id="session") == message
    with pytest.raises(EntryConflict):
        await history.put(
            actor=actor,
            session_id="session",
            key="one",
            message=Message(role="user", content="changed"),
        )
    for owner, session in ((Actor(id="other"), "session"), (actor, "other")):
        with pytest.raises(PermissionError):
            await history.read(ref, actor=owner, session_id=session)
    with pytest.raises(ValueError, match="integrity"):
        await history.read(
            ref.model_copy(update={"sha256": "incorrect"}), actor=actor, session_id="session"
        )
    assert history.durability == "none"


async def test_policy_and_base_provider_defaults_conformance():
    actor, session = Actor(id="owner"), SessionState(id="session", owner="owner")
    spec = AgentSpec(id="demo", version="1", title="Demo", instructions="Demo", model="demo")
    decision = await AllowAll().authorize(
        AuthzRequest(actor=actor, action="call_tool", resource="tool")
    )
    assert decision.allowed
    provider = BaseProvider()
    assert provider.tools() == ()
    assert await provider.capabilities(actor, session) == {}
    assert await provider.hidden_tools(session, spec) == frozenset()
    assert await provider.prompt_section(session, spec) is None
    call = ToolCall(id="call", name="write", arguments={})
    message = Message(role="tool", content="fabricated", tool_call_id="call")
    assert await provider.before_call(session, spec, call) is None
    assert await provider.after_call(session, spec, call, message) == message
    assert await provider.before_request(session, spec, (message,)) == (message,)


def test_registry_refuses_conflicts_and_invalid_definitions():
    with pytest.raises(ValueError, match="provider_conflict"):
        Registry((DemoTool(), DemoTool()), ())
    with pytest.raises(ValueError, match="provider_conflict"):
        Registry((), (BaseProvider(), BaseProvider()))
    provider = BaseProvider()
    provider.contract = 2
    assert Registry((), (provider,)).providers == (provider,)
    provider.contract = 3
    assert Registry((), (provider,)).providers == (provider,)
    provider.contract = 4
    with pytest.raises(ValueError, match="provider_conflict"):
        Registry((), (provider,))
    tool = DemoTool()
    tool.version = ""
    with pytest.raises(ValueError, match="invalid_tool_definition"):
        Registry((tool,), ())


def test_structural_narrowing_preserves_call_bindings():
    user = Message(role="user", content="fabricated")
    call = ToolCall(id="call", name="write", arguments={"count": 3})
    assistant = Message(role="assistant", content="fabricated", tool_calls=(call,))
    assert structural_subset((user, assistant), (assistant,)) == (assistant,)
    changed = assistant.model_copy(
        update={"tool_calls": (call.model_copy(update={"id": "other"}),)}
    )
    with pytest.raises(ExecutionDenied, match="provider_output_invalid"):
        structural_subset((user, assistant), (changed,))
