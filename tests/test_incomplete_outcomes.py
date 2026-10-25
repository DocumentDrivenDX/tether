"""Incomplete observed text is durable evidence, never Agent execution authority."""

import pytest
from test_gated_execution import Harness, proposal

from tether.engine.reconstruction import restored_usage
from tether.types import ModelResponse, TokenUsage

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("reason", ["length", "filtered", "unknown"])
@pytest.mark.parametrize("calls", [False, True])
@pytest.mark.parametrize("text", ["", "Fabricated incomplete text. é"])
async def test_incomplete_terminal_preserves_text_usage_without_dispatch(
    limits, reason, calls, text
):
    usage = TokenUsage(input_tokens=7, output_tokens=3)
    response = ModelResponse(
        content=text,
        finish_reason=reason,
        tool_calls=proposal().tool_calls if calls else (),
        usage=usage,
    )
    h = Harness(limits, script=[response])
    try:
        result = await h.run()
        assert (result.stop_reason, result.reason) == ("model_incomplete", reason)
        assert len(h.model.requests) == 1
        assert not h.tool.calls
        assert not result.pending
        assert result.messages[-1].content == response.content
        assert not result.messages[-1].is_error
        assert not result.messages[-1].tool_calls
        call = h.facts("model_call")[-1]
        assert call.facts["status"] == "incomplete"
        assert call.facts["finish_reason"] == reason
        assert call.facts["usage"] == usage.model_dump(mode="json")
        source = h.facts("message")[-1]
        assert not source.call_manifest
        assert source.facts["model_status"] == "incomplete"
        assert not h.facts("tool_call")
        rebuilt = restored_usage(h.log.entries, result.turn_id)
        assert rebuilt.input_tokens == 7
        assert rebuilt.output_tokens == 3
        assert h.facts("turn_ended")[-1].facts["stop_reason"] == "model_incomplete"
    finally:
        assert await h.engine.aclose()


async def test_next_turn_uses_original_incomplete_text_without_executable_calls(limits):
    from test_gated_execution import final

    h = Harness(limits, script=[ModelResponse(content="Partial", finish_reason="length"), final()])
    try:
        assert (await h.run()).stop_reason == "model_incomplete"
        assert (await h.run()).stop_reason == "final"
        prior = next(m for m in h.model.requests[-1].messages if m.content == "Partial")
        assert not prior.tool_calls
        assert len(h.model.requests) == 2
        assert not h.tool.calls
    finally:
        assert await h.engine.aclose()


@pytest.mark.parametrize("prior", [False, True])
async def test_forged_incomplete_signal_cannot_invent_acceptance_or_diagnostics(limits, prior):
    from tether.runtime.pydantic_ai.response import IncompleteModelResponse

    injected = "Fabricated private diagnostic"
    script = ([proposal()] if prior else []) + [IncompleteModelResponse(injected)]
    h = Harness(limits, script=script)
    try:
        result = await h.run()
        assert (result.stop_reason, result.reason) == ("model_unavailable", "invalid_terminal")
        assert not any(e.facts.get("status") == "incomplete" for e in h.log.entries)
        assert injected not in str([e.model_dump() for e in h.log.entries])
        assert injected not in str(result.model_dump())
        assert len(h.tool.calls) == int(prior)
        assert len(h.model.requests) == 1 + int(prior)
    finally:
        assert await h.engine.aclose()


async def test_cancel_during_incomplete_text_disclosure_records_cancelled(limits):
    import asyncio

    h = Harness(limits, script=[ModelResponse(content="Partial", finish_reason="length")])
    entered = asyncio.Event()
    original = h.history.read

    async def blocked(ref, **values):
        if ref.key.startswith("model:"):
            entered.set()
            await asyncio.Event().wait()
        return await original(ref, **values)

    h.history.read = blocked
    task = asyncio.create_task(h.run())
    try:
        async with asyncio.timeout(2):
            await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert h.facts("model_call")[-1].facts["status"] == "incomplete"
        assert h.facts("turn_ended")[-1].facts["stop_reason"] == "cancelled"
        assert not h.tool.calls
        from tether.engine.recovery import plan_resume
        from tether.types.execution import NotInterrupted

        with pytest.raises(NotInterrupted):
            plan_resume(h.log.entries, {}, session_id=h.log.session.id)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert await h.engine.aclose()


@pytest.mark.parametrize("unknown", [False, True])
async def test_failed_incomplete_acceptance_never_returns_an_accepted_result(limits, unknown):
    from tether.types.execution import CommitUnknown
    from tether.types.storage import StoreError

    h = Harness(limits, script=[ModelResponse(content="Partial", finish_reason="length")])
    append = h.log.append

    async def fail(entries):
        if any(e.kind == "model_call" and e.facts.get("status") == "incomplete" for e in entries):
            if unknown:
                await append(entries)
                raise CommitUnknown("fabricated_unknown")
            raise StoreError("fabricated_refusal")
        return await append(entries)

    h.log.append = fail
    try:
        with pytest.raises(CommitUnknown if unknown else StoreError):
            await h.run()
        assert not h.facts("turn_ended")
        assert not h.tool.calls
        assert bool(h.facts("model_call")) is unknown
        if unknown:
            assert h.facts("model_call")[-1].facts["status"] == "incomplete"
    finally:
        assert await h.engine.aclose()


async def test_incomplete_disclosure_requires_current_read_authorization(limits):
    from test_gated_execution import MutablePolicy

    policy = MutablePolicy()
    h = Harness(
        limits, script=[ModelResponse(content="Partial", finish_reason="length")], policy=policy
    )
    append = h.log.append

    async def revoke(entries):
        accepted = await append(entries)
        if any(e.kind == "model_call" and e.facts.get("status") == "incomplete" for e in entries):
            policy.deny.add("read_session")
        return accepted

    h.log.append = revoke
    try:
        result = await h.run()
        assert result.stop_reason == "model_denied"
        assert not result.messages
        assert h.facts("model_call")[-1].facts["status"] == "incomplete"
        assert not h.tool.calls
    finally:
        assert await h.engine.aclose()


@pytest.mark.parametrize(
    "fault", ["duplicate", "unregistered", "too_large", "stop_calls", "no_calls"]
)
async def test_nonexecutable_acceptance_does_not_relax_structural_limits(limits, fault):
    response = proposal().model_copy(update={"content": "Partial", "finish_reason": "length"})
    changes = {
        "duplicate": {"tool_calls": response.tool_calls * 2},
        "unregistered": {
            "tool_calls": (response.tool_calls[0].model_copy(update={"name": "missing"}),)
        },
        "too_large": {"content": "x" * limits.max_response_bytes},
        "stop_calls": {"finish_reason": "stop"},
        "no_calls": {"finish_reason": "tool_calls", "tool_calls": ()},
    }
    h = Harness(limits, script=[response.model_copy(update=changes[fault])])
    try:
        result = await h.run()
        assert result.stop_reason == "model_unavailable"
        assert not result.messages
        assert h.facts("model_call")[-1].facts["status"] == "error"
        assert not h.tool.calls
        assert not any(e.call_manifest for e in h.log.entries)
    finally:
        assert await h.engine.aclose()
