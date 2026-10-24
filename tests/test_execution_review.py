"""Regression review: sources, batch pause, mutation, workers and effect barriers."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from datetime import date
from threading import Event

import pytest
from pydantic import BaseModel, field_validator
from test_gated_execution import DemoProvider, DemoTool, Harness, MutablePolicy, final, proposal

from tether.engine.workers import Workers
from tether.types import Message, ModelResponse, ToolCall
from tether.types.execution import Actor, Answer, ExecutionDenied, ExecutionLimits
from tether.types.tooling import normalize

pytestmark = pytest.mark.anyio


def replace_hook(provider, name, function):
    setattr(provider, name, function)


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_batch_stops_at_first_approval_and_continues_serially(limits):
    batch = ModelResponse(
        tool_calls=(proposal().tool_calls[0], proposal(count="4", call_id="second").tool_calls[0]),
        finish_reason="tool_calls",
    )
    h = Harness(limits, manual=True, script=[batch, final()])
    first = await h.run()
    assert len(first.pending) == 1
    assert not h.tool.calls
    second = await h.answer(first)
    assert second is not None
    assert second.stop_reason == "confirmation_required"
    assert [c[1]["count"] for c in h.tool.calls] == [3]
    last = await h.answer(second)
    assert last is not None
    assert last.stop_reason == "final"
    assert [c[1]["count"] for c in h.tool.calls] == [3, 4]
    assert h.tool.calls[0][0] != h.tool.calls[1][0]


async def test_call_id_reuse_never_reuses_invocation_or_approval(limits):
    h = Harness(limits, script=[proposal(), proposal(), final()])
    assert (await h.run()).stop_reason == "final"
    assert len(h.tool.calls) == 2
    assert h.tool.calls[0][0] != h.tool.calls[1][0]


@pytest.mark.parametrize("change", ["version", "schema", "connected"])
async def test_changed_pinned_tool_never_uses_old_approval(limits, change):
    h = Harness(limits, manual=True)
    pending = await h.run()
    if change == "version":
        h.tool.version = "2"
    elif change == "connected":
        h.tool.definition_hash = "different"
    else:

        class Different(BaseModel):
            count: int = 8

        h.tool.input_model = Different
    await h.answer(pending)
    assert not h.tool.calls
    assert h.facts("tool_call")[-1].facts["reason"] in {"definition_changed", "approval_invalid"}


async def test_nondeterministic_normalization_invalidates_approval(limits):
    values = [0]

    class ChangedDefault(BaseModel):
        count: int

        @field_validator("count")
        @classmethod
        def change(cls, value):
            values[0] += 1
            return value + values[0]

    tool = DemoTool()
    tool.input_model = ChangedDefault
    h = Harness(limits, tool=tool, manual=True)
    pending = await h.run()
    await h.answer(pending)
    assert not tool.calls
    assert h.facts("tool_call")[-1].facts["reason"] == "approval_invalid"


async def test_source_revocation_before_approval_execution_fails_closed(limits):
    h = Harness(limits, manual=True)
    pending = await h.run()
    policy = MutablePolicy()
    policy.deny.add("read_session")
    h.engine.policy = policy
    h.engine._runs[h.log.session.id].gov.policy = policy
    answered = await h.answer(pending)
    assert answered is not None
    assert answered.stop_reason == "model_denied"
    assert not h.tool.calls


async def test_forged_sdk_arguments_fail_original_source_binding(limits):
    h = Harness(limits, manual=True)
    pending = await h.run()
    run = h.engine._runs[h.log.session.id]
    run.executor.pending.clear()
    message = await run.executor.execute(ToolCall(id="call", name="write", arguments={"count": 99}))
    assert message is not None
    assert message.content == "raw_proposal_changed"
    assert not h.tool.calls
    assert pending.pending


async def test_new_turn_supersedes_pending_and_seals_group(limits):
    h = Harness(limits, manual=True, script=[proposal(), final()])
    pending = await h.run()
    result = await h.run()
    assert result.stop_reason == "final"
    assert not h.tool.calls
    assert h.facts("confirmation")[-1].facts["state"] == "superseded"
    assert await h.answer(pending) is None


async def test_cancelled_effect_is_closed_and_barrier_survives_next_turn(limits):
    h = Harness(limits, script=[proposal(), proposal(call_id="new"), final()])
    entered = asyncio.Event()

    async def run(ctx, args):
        entered.set()
        await asyncio.Event().wait()
        return "unreachable"

    h.tool.run = run
    task = asyncio.create_task(h.run())
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert h.facts("turn_ended")[-1].facts["stop_reason"] == "cancelled"
    assert (await h.run()).stop_reason == "final"
    assert h.facts("tool_call")[-1].facts["reason"] == "effect_unresolved"
    assert any(e.facts.get("reason") == "outcome_unknown" for e in h.facts("tool_call"))


async def test_unknown_effect_allows_separate_read_only_safe_tool(limits):
    reader = DemoTool()
    reader.name, reader.effect, reader.replay = "read", "read_only", "safe"
    h = Harness(
        limits,
        extra_tools=(reader,),
        script=[proposal(), proposal(name="read", call_id="read"), final()],
    )
    h.tool.failure = RuntimeError("fabricated")
    await h.run()
    assert len(reader.calls) == 1
    assert len([e for e in h.facts("tool_call") if e.facts["status"] == "started"]) == 1


async def test_read_only_unsafe_failure_does_not_create_effect_barrier(limits):
    reader = DemoTool()
    reader.name, reader.effect = "read", "read_only"
    reader.failure = ValueError("fabricated")
    h = Harness(
        limits,
        extra_tools=(reader,),
        script=[proposal(name="read"), proposal(call_id="write"), final()],
    )
    await h.run()
    assert len(h.tool.calls) == 1
    assert any(e.facts.get("reason") == "tool_error" for e in h.facts("tool_call"))


async def test_always_ask_receives_normalized_isolated_values(limits):
    tool = DemoTool()
    tool.always_ask = True
    seen = []

    def asks(ctx, args):
        seen.append(args.count)
        args.count = 999
        return True

    tool.asks_first = asks
    h = Harness(limits, tool=tool)
    pending = await h.run()
    assert seen == [3]
    await h.answer(pending)
    assert tool.calls[0][1]["count"] == 3


@pytest.mark.parametrize("fault", ["raises", "role", "error", "late_content"])
async def test_after_hook_cannot_forge_result_shape_or_leak_unguarded_content(limits, fault):
    provider = DemoProvider()

    async def after(session, spec, call, message):
        if fault == "raises":
            raise ValueError("fabricated secret content")
        updates = {"role": "assistant"} if fault == "role" else {"is_error": True}
        if fault == "late_content":
            updates = {"content": "x" * 8192}
        return message.model_copy(update=updates)

    provider.after_call = after
    h = Harness(limits, providers=(provider,), limits=ExecutionLimits(max_result_bytes=512))
    await h.run()
    assert h.model.requests[-1].messages[-1].is_error
    assert "secret content" not in str(h.log.entries)


async def test_mediated_read_and_capabilities_use_current_policy(limits):
    provider = DemoProvider()
    replace_hook(provider, "capabilities", lambda actor, session: {str: "fabricated capability"})
    policy = MutablePolicy()
    policy.deny.add("retrieve")
    h = Harness(limits, providers=(provider,), policy=policy)
    fetched = []

    async def tool_run(ctx, args):
        assert ctx.capability(str) == "fabricated capability"
        with pytest.raises(ExecutionDenied, match="capability_unavailable"):
            ctx.capability(int)
        return await ctx.read("fabricated-resource", lambda: fetched.append(True) or "value")

    h.tool.run = tool_run
    await h.run()
    assert not fetched
    assert any(
        e.facts.get("action") == "retrieve" and e.facts["allowed"] is False
        for e in h.facts("authorization")
    )


async def test_mediated_read_success(limits):
    h = Harness(limits)

    async def tool_run(ctx, args):
        return await ctx.read("fabricated-resource", lambda: "fabricated value")

    h.tool.run = tool_run
    await h.run()
    assert h.model.requests[-1].messages[-1].content == "fabricated value"


@pytest.mark.parametrize("fault", ["widen", "bad_type", "hidden", "prompt", "capabilities"])
async def test_provider_request_narrowing_cannot_widen_or_dispatch_hidden_tools(limits, fault):
    provider = DemoProvider()
    if fault == "widen":
        replace_hook(
            provider,
            "before_request",
            lambda session, spec, messages: (
                *messages,
                Message(role="user", content="injected"),
            ),
        )
    elif fault == "bad_type":
        replace_hook(provider, "before_request", lambda session, spec, messages: list(messages))
    elif fault == "hidden":
        replace_hook(provider, "hidden_tools", lambda session, spec: frozenset({"write"}))
    elif fault == "prompt":
        replace_hook(provider, "prompt_section", lambda session, spec: 12)
    else:
        replace_hook(provider, "capabilities", lambda actor, session: {"invalid": "value"})
    h = Harness(limits, providers=(provider,))
    result = await h.run()
    assert not h.tool.calls
    assert result.stop_reason in {"model_denied", "model_unavailable", "final"}


async def test_provider_redactions_and_prompt_sections_are_narrow_and_isolated(limits):
    provider = DemoProvider()
    replace_hook(provider, "prompt_section", lambda session, spec: "Fabricated section")
    replace_hook(
        provider,
        "before_request",
        lambda session, spec, messages: tuple(
            m.model_copy(update={"content": "redacted"}) for m in messages
        ),
    )
    h = Harness(limits, providers=(provider,), script=[final()])
    await h.run()
    assert h.model.requests[0].system.endswith("Fabricated section")
    assert h.model.requests[0].messages[0].content == "redacted"
    original = next(e for e in h.facts("message") if not e.call_manifest)
    assert original.history_ref is not None
    assert (
        await h.history.read(original.history_ref, actor=h.actor, session_id=h.log.session.id)
    ).content == "Fabricated request"


async def test_policy_exception_is_denial_and_records_no_content(limits):
    h = Harness(limits)
    h.engine.policy = MutablePolicy()
    h.engine.policy.fail = True
    with pytest.raises(ExecutionDenied, match="policy_unavailable"):
        await h.run()
    assert "fabricated policy content" not in str(h.log.entries)
    assert not h.model.requests


async def test_bounded_workers_hold_capacity_until_actual_exit_and_copy_context():
    workers = Workers(1)
    entered, release = Event(), Event()
    marker = ContextVar("fabricated_marker", default="missing")
    marker.set("copied")

    def work():
        entered.set()
        if not release.wait(2):
            raise TimeoutError("test_worker_release")
        return marker.get()

    first = asyncio.create_task(workers.call("session", work))
    try:
        await asyncio.wait_for(asyncio.to_thread(entered.wait), 2)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert workers.busy("session")
        with pytest.raises(ExecutionDenied, match="worker_capacity"):
            await workers.call("other", lambda: None)
    finally:
        release.set()
        await asyncio.gather(*tuple(workers.active))
    assert not workers.busy("session")
    assert await workers.call("session", lambda: marker.get()) == "copied"


async def test_plain_def_returning_awaitable_is_supported():
    workers = Workers(1)

    async def value():
        return "fabricated"

    assert await workers.call("session", value) == "fabricated"
    assert await workers.call("session", lambda: value()) == "fabricated"


async def test_non_json_tool_values_and_expanded_defaults_refused_before_approval():
    class DateArgs(BaseModel):
        count: date

    tool = DemoTool()
    tool.input_model = DateArgs
    with pytest.raises(ExecutionDenied, match="invalid_arguments"):
        normalize(tool, {"count": "2026-01-01"}, 1024)
    tool = DemoTool()
    with pytest.raises(ExecutionDenied, match="invalid_arguments"):
        normalize(tool, {"count": 3}, 16)
    with pytest.raises(ExecutionDenied, match="invalid_arguments"):
        normalize(tool, {"count": "x" * 1024}, 512)


async def test_wrong_actor_and_foreign_invocation_never_execute(limits):
    h = Harness(limits, manual=True)
    pending = await h.run()
    assert await h.engine.answer(h.actor, h.log, "foreign", Answer(approved=True)) is None
    with pytest.raises(ExecutionDenied, match="denied"):
        await h.engine.answer(
            Actor(id="other"), h.log, pending.pending[0].invocation_id, Answer(approved=True)
        )
    assert not h.tool.calls


async def test_cancelled_queued_worker_never_calls_function(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def queued(function):
        entered.set()
        await release.wait()
        return function()

    monkeypatch.setattr(asyncio, "to_thread", queued)
    workers = Workers(1)
    waiting = asyncio.create_task(workers.call("session", lambda: calls.append(True)))
    await asyncio.wait_for(entered.wait(), 2)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    release.set()
    await asyncio.gather(*tuple(workers.active), return_exceptions=True)
    assert not calls
    assert not workers.busy("session")


async def test_worker_shutdown_is_finite_and_reports_outstanding_work():
    workers = Workers(1)
    entered, release = Event(), Event()

    def work():
        entered.set()
        return release.wait(2)

    task = asyncio.create_task(workers.call("session", work))
    try:
        await asyncio.wait_for(asyncio.to_thread(entered.wait), 2)
        assert await workers.close(0.001) is False
        with pytest.raises(ExecutionDenied, match="engine_closed"):
            await workers.call("new", lambda: None)
    finally:
        release.set()
        assert await task
    assert await workers.close(0.001) is True


async def test_sync_tool_cancellation_blocks_new_turn_until_actual_exit(limits):
    h = Harness(limits)
    entered, release = Event(), Event()

    def run(ctx, args):
        entered.set()
        release.wait(2)
        return "fabricated"

    h.tool.run = run
    task = asyncio.create_task(h.run())
    try:
        await asyncio.wait_for(asyncio.to_thread(entered.wait), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(ExecutionDenied, match="worker_outstanding"):
            await h.run()
    finally:
        release.set()
        await asyncio.gather(*tuple(h.engine.workers.active))
    assert await h.engine.aclose()
    with pytest.raises(ExecutionDenied, match="engine_closed"):
        await h.run()


async def test_custom_serializer_cannot_change_approved_executable_values():
    from pydantic import field_serializer

    class Serialized(BaseModel):
        count: int

        @field_serializer("count")
        def changed(self, count):
            return count + 10

    tool = DemoTool()
    tool.input_model = Serialized
    with pytest.raises(ExecutionDenied, match="invalid_arguments"):
        normalize(tool, {"count": 3}, 1024)


async def test_nested_typed_json_arguments_remain_lossless():
    class Child(BaseModel):
        count: int

    class Nested(BaseModel):
        child: Child
        labels: list[str]
        extra: dict[str, str]

    tool = DemoTool()
    tool.input_model = Nested
    args = normalize(tool, {"child": {"count": "3"}, "labels": ["demo"], "extra": {}}, 1024)
    assert args.arguments == {"child": {"count": 3}, "labels": ["demo"], "extra": {}}


async def test_empty_agent_stop_is_typed_without_dispatch(limits):
    h = Harness(limits, script=[ModelResponse(content="", finish_reason="stop")])
    result = await h.run()
    assert result.stop_reason == "model_unavailable"
    assert not h.tool.calls
    assert len(h.model.requests) == 1


async def test_concurrent_approval_answer_has_one_winner(limits):
    h = Harness(limits, manual=True)
    pending = await h.run()
    results = await asyncio.gather(h.answer(pending), h.answer(pending))
    assert sum(r is not None for r in results) == 1
    assert len(h.tool.calls) == 1
    assert len([e for e in h.log.entries if e.key and e.key.startswith("approval:")]) == 1


async def test_unacknowledged_intent_does_not_dispatch(limits):
    h = Harness(limits)
    append = h.log.append

    async def reject(entries):
        if any(e.key and e.key.startswith("intent:") for e in entries):
            return ()
        return await append(entries)

    h.log.append = reject
    result = await h.run()
    assert result.reason == "commit_not_accepted"
    assert not h.tool.calls
    assert len(h.model.requests) == 1


async def test_policy_reason_allowlist_prevents_content_shaped_code_leak(limits):
    h = Harness(limits)

    async def authorize(request):
        from tether.types.execution import Decision

        return Decision(allowed=False, reason="fabricatedcustomername")

    h.engine.policy.authorize = authorize
    with pytest.raises(ExecutionDenied, match="policy_reason_invalid"):
        await h.run()
    assert "fabricatedcustomername" not in str(h.log.entries)


async def test_credentials_are_not_deepcopied_or_recorded(limits):
    class Credential:
        def __deepcopy__(self, memo):
            raise TypeError("credentials must retain their live identity")

        async def authorization(self):
            return "fabricated"

    h = Harness(limits, providers=(DemoProvider(),))
    credential = Credential()
    h.actor = h.actor.model_copy(update={"credential": credential})
    observed = []
    original = h.model.stream

    def stream(request):
        observed.append(request.credential)
        return original(request)

    h.model.stream = stream
    assert (await h.run()).stop_reason == "final"
    assert observed[0] is credential
    assert h.model.requests[0].credential is None
    assert "credential" not in str(h.log.entries).lower()


async def test_dropping_tool_result_cannot_send_incomplete_group_to_model(limits):
    provider = DemoProvider()
    replace_hook(
        provider,
        "before_request",
        lambda session, spec, messages: tuple(m for m in messages if m.role != "tool"),
    )
    h = Harness(limits, providers=(provider,))
    result = await h.run()
    assert result.stop_reason == "model_unavailable"
    assert result.reason == "invalid_history"
    assert len(h.model.requests) == 1
