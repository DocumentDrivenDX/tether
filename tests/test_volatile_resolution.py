"""The volatile execution ports retain the same scoped-source boundary."""

import pytest
from test_gated_execution import Harness

from tether.engine.effects import unresolved_effects
from tether.testing.execution import MemoryHistory
from tether.types import Message
from tether.types.execution import Actor, Resolution

pytestmark = pytest.mark.anyio


async def test_volatile_unknown_result_can_be_resolved_without_reexecution(limits):
    h = Harness(limits)
    h.tool.failure = RuntimeError("fabricated_effect_failure")
    await h.run()
    intent = next(e for e in h.log.entries if e.key and e.key.startswith("intent:"))
    invocation_id = intent.facts["invocation_id"]
    assert isinstance(invocation_id, str)
    value = Resolution(invocation_id=invocation_id, outcome="completed", reason="operator_checked")
    calls = len(h.tool.calls), len(h.model.requests)
    assert unresolved_effects(h.log.entries)
    await h.engine.resolve(h.actor, h.log, value)
    assert not unresolved_effects(h.log.entries)
    before = h.log.entries
    await h.engine.resolve(h.actor, h.log, value)
    assert h.log.entries == before
    assert all(
        (e.history_ref.owner, e.history_ref.session_id, e.history_ref.kind)
        == (h.actor.id, h.log.session.id, "message")
        for e in h.log.entries
        if e.history_ref is not None
    )
    assert (len(h.tool.calls), len(h.model.requests)) == calls
    assert await h.engine.aclose()


@pytest.mark.parametrize(
    "change",
    [
        {"owner": "other"},
        {"session_id": "other"},
        {"kind": "summary"},
        {"component": "assistant"},
        {"codec": "unknown"},
    ],
)
async def test_memory_history_ref_scope_and_profile_are_verified(change):
    history, actor = MemoryHistory(), Actor(id="owner")
    ref = await history.put(
        actor=actor,
        session_id="session",
        key="fabricated",
        message=Message(role="user", content="Fabricated"),
    )
    with pytest.raises(PermissionError, match="history_denied"):
        await history.read(ref.model_copy(update=change), actor=actor, session_id="session")
