"""Registry publication, specification boundaries and independent edited bindings."""

import asyncio
from typing import cast

import pytest
from test_gated_execution import DemoTool, Harness

from tether.testing.approval_edits import NarrowCount
from tether.types.canonical import digest
from tether.types.execution import Answer, ExecutionDenied, Resolution

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_registry_publication_is_atomic_and_pending_definition_replacement_cannot_dispatch(
    limits,
):
    h = Harness(limits, manual=True)
    result = await h.run()
    old = h.engine.registry
    replacement = DemoTool()
    replacement.version = "2"
    generation = h.engine.tool_registry.publish((replacement,))
    assert generation != old.generation
    assert old.tools["write"].tool.version == "1"
    with pytest.raises(TypeError):
        cast(dict[str, object], old.tools)["other"] = old.tools["write"]
    with pytest.raises(AttributeError, match="immutable_tool_generation"):
        old.tools["write"].tool.version = "changed"
    with pytest.raises(ValueError, match="provider_conflict"):
        h.engine.tool_registry.publish((replacement, replacement))
    assert h.engine.registry.generation == generation
    await h.answer(result)
    assert h.tool.calls == []
    assert replacement.calls == []
    assert any(entry.facts.get("reason") == "approval_invalid" for entry in h.facts("tool_call"))
    await h.engine.aclose()


async def test_registry_retains_generation_until_actual_cancelled_worker_exits(limits):
    entered, release = asyncio.Event(), asyncio.Event()
    tool = DemoTool()

    async def blocked(ctx, args):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return "Fabricated"

    tool.run = blocked
    h = Harness(limits, tool=tool)
    task = asyncio.create_task(h.run())
    await entered.wait()
    old = h.engine.registry.generation
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    h.engine.tool_registry.max_generations = 1
    replacement = DemoTool()
    replacement.version = "2"
    with pytest.raises(ExecutionDenied, match="registry_capacity"):
        h.engine.tool_registry.publish((replacement,))
    assert h.engine.registry.generation == old
    release.set()
    await asyncio.gather(*tuple(h.engine.workers.active), return_exceptions=True)
    with pytest.raises(ExecutionDenied, match="registry_capacity"):
        h.engine.tool_registry.publish((replacement,))
    invocation = next(
        e.facts["invocation_id"] for e in h.facts("tool_call") if e.facts["status"] == "started"
    )
    assert isinstance(invocation, str)
    await h.engine.resolve(
        h.actor,
        h.log,
        Resolution(invocation_id=invocation, outcome="completed", reason="fabricated"),
    )
    h.engine.tool_registry.publish((replacement,))
    await h.engine.aclose()


async def test_edited_approval_preserves_proposal_shown_and_normalized_executable_bindings(limits):
    h = Harness(limits, manual=True, approval_transforms={"write": NarrowCount()})
    pending = (await h.run()).pending[0]
    result = await h.engine.answer(
        h.actor, h.log, pending.invocation_id, Answer(approved=True, arguments={"count": 1})
    )
    assert result is not None
    assert result.stop_reason == "final"
    assert h.tool.calls[0][1] == {"count": 1, "label": "fabricated-default"}
    approval = next(e for e in h.log.entries if e.key == "approval:" + pending.invocation_id)
    binding = approval.facts["binding"]
    assert isinstance(binding, dict)
    assert binding["proposal_digest"] == digest({"count": "3"})
    assert binding["shown_digest"] == digest({"count": 1})
    assert binding["executable_digest"] == digest({"count": 1, "label": "fabricated-default"})
    assert binding["args_digest"] == binding["executable_digest"]
    source = next(e for e in h.log.entries if e.call_manifest)
    assert source.history_ref is not None
    original = await h.history.read(source.history_ref, actor=h.actor, session_id=h.log.session.id)
    assert original.tool_calls[0].arguments == {"count": "3"}
    await h.engine.aclose()


@pytest.mark.parametrize("mode", ["expand", "mutate", "raise", "drift"])
async def test_invalid_edit_cannot_create_an_intent_or_execute(limits, mode):
    class BadTransform(NarrowCount):
        calls = 0

        async def transform(self, request):
            self.calls += 1
            if mode == "mutate":
                request.original["count"] = 99
            if mode == "raise":
                raise RuntimeError("Fabricated failure")
            result = await super().transform(request)
            if mode == "drift" and self.calls > 1:
                result["count"] = 0
            return result

    h = Harness(limits, manual=True, approval_transforms={"write": BadTransform()})
    pending = (await h.run()).pending[0]
    if mode == "drift":
        await h.engine.answer(
            h.actor, h.log, pending.invocation_id, Answer(approved=True, arguments={"count": 1})
        )
    else:
        with pytest.raises(ExecutionDenied, match="approval_edit_invalid"):
            await h.engine.answer(
                h.actor,
                h.log,
                pending.invocation_id,
                Answer(approved=True, arguments={"count": 4 if mode == "expand" else 1}),
            )
    assert not h.tool.calls
    assert not any(e.key and e.key.startswith("intent:") for e in h.log.entries)
    await h.engine.aclose()
