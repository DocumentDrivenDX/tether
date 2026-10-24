"""Actual Agent characterization against fabricated volatile execution ports."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from pydantic import BaseModel, JsonValue

from tether.context.testing import fabricated_context
from tether.engine import Engine
from tether.engine.governance import AllowAll
from tether.engine.providers import BaseProvider
from tether.models.testing import ListAttemptRecorder, ScriptedModel
from tether.testing.execution import MemoryHistory, MemoryTurnLog
from tether.types import ModelResponse, ModelUnavailable, ToolCall
from tether.types.execution import (
    Actor,
    AgentSpec,
    Answer,
    AuthzRequest,
    CommitUnknown,
    Decision,
    Effect,
    ExecutionDenied,
    ExecutionLimits,
    GuardVerdict,
    Refusal,
    Replay,
    SessionState,
    ToolContext,
    ToolOutput,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Args(BaseModel):
    count: int
    label: str = "fabricated-default"


class DemoTool:
    name = "write"
    version = "1"
    description = "Fabricated write."
    input_model: type[BaseModel] = Args
    requires = frozenset({"workspace"})
    model_visible = True
    always_ask = False
    effect: Effect = "effectful"
    replay: Replay = "unsafe"
    timeout_seconds: float | None = None
    definition_hash: str | None = None

    def __init__(self):
        self.traits: dict[str, JsonValue] = {}
        self.calls = []
        self.failure: Exception | None = None

    def asks_first(self, ctx: ToolContext, args: BaseModel) -> bool:
        return True

    async def run(self, ctx: ToolContext, args: BaseModel) -> str | ToolOutput:
        self.calls.append((ctx.invocation_id, args.model_dump()))
        if self.failure:
            raise self.failure
        return f"Wrote {cast(Args, args).count}"


class DemoProvider(BaseProvider):
    id = "demo"
    contract = 1

    def __init__(self):
        self.before = []
        self.after = []
        self.refusal: Refusal | None = None
        self.fail = False
        self.redact = False
        self.mutate = False

    async def before_call(self, session, spec, call):
        self.before.append(call.model_copy(deep=True))
        if self.fail:
            raise RuntimeError("fabricated private content")
        if self.mutate:
            call.arguments["count"] = 999
        return self.refusal

    async def after_call(self, session, spec, call, message):
        self.after.append(call.model_copy(deep=True))
        if self.redact:
            return message.model_copy(update={"content": "redacted"})
        return message


class DemoGuard:
    id = "demo"

    def __init__(self, deny=False, fail=False):
        self.seen = []
        self.deny, self.fail = deny, fail

    async def check(self, value, session):
        self.seen.append(value.output.text)
        if self.fail:
            raise RuntimeError("fabricated guard content")
        return GuardVerdict(outcome="withheld" if self.deny else "ok")


class MutablePolicy(AllowAll):
    def __init__(self):
        self.deny = set()
        self.fail = False
        self.seen = []

    async def authorize(self, request: AuthzRequest):
        self.seen.append(request)
        if self.fail:
            raise RuntimeError("fabricated policy content")
        return Decision(allowed=request.action not in self.deny, reason="allowed")


class Harness:
    def __init__(self, model_limits, *, manual=False, script=None, **options):
        self.actor = Actor(id="fabricated-owner")
        self.log = MemoryTurnLog(
            SessionState(id="session", owner=self.actor.id, approval="manual" if manual else "auto")
        )
        self.history = MemoryHistory()
        self.tool = options.pop("tool", DemoTool())
        self.model = ScriptedModel(script or [proposal(), final()])
        self.engine = Engine(
            **fabricated_context(),
            model=self.model,
            history=self.history,
            model_limits=model_limits,
            tools=(self.tool, *options.pop("extra_tools", ())),
            attempt_factory=ListAttemptRecorder,
            **options,
        )
        self.spec = AgentSpec(
            id="demo",
            version="1",
            title="Demo",
            instructions="Fabricated.",
            model="demo",
            tools=tuple(self.engine.registry.tools),
        )

    async def run(self):
        return await self.engine.run_turn(
            self.actor, self.log, "Fabricated request", spec=self.spec
        )

    def facts(self, kind):
        return [e for e in self.log.entries if e.kind == kind]

    async def answer(self, result, approved=True):
        return await self.engine.answer(
            self.actor, self.log, result.pending[0].invocation_id, Answer(approved=approved)
        )


def proposal(*, count="3", call_id="call", name="write"):
    return ModelResponse(
        tool_calls=(ToolCall(id=call_id, name=name, arguments={"count": count}),),
        finish_reason="tool_calls",
    )


def final():
    return ModelResponse(content="Fabricated done", finish_reason="stop")


async def test_real_agent_normalizes_once_and_commits_before_dispatch(limits):
    provider = DemoProvider()
    h = Harness(limits, providers=(provider,))
    result = await h.run()
    assert result.stop_reason == "final"
    assert h.tool.calls[0][1] == {"count": 3, "label": "fabricated-default"}
    assert provider.before[0].arguments == provider.after[0].arguments == h.tool.calls[0][1]
    intents = [e for e in h.facts("tool_call") if e.facts["status"] == "started"]
    results = [e for e in h.facts("tool_call") if e.facts["status"] == "ok"]
    source = next(e for e in h.facts("message") if e.call_manifest)
    assert source.index < intents[0].index < results[0].index
    assert source.call_manifest[0].args_digest != intents[0].facts["args_digest"]
    assert "Fabricated request" not in str([e.model_dump() for e in h.log.entries])


@pytest.mark.parametrize("count", ["invalid", {}, None])
async def test_invalid_arguments_are_typed_and_never_execute(limits, count):
    h = Harness(limits, script=[proposal(count=count), final()])
    assert (await h.run()).stop_reason == "final"
    assert not h.tool.calls
    assert h.facts("tool_call")[-1].facts["reason"] == "invalid_arguments"
    assert h.model.requests[-1].messages[-1].is_error


@pytest.mark.parametrize("action", ["call_tool", "call_model", "read_session"])
async def test_current_policy_denials_cannot_be_granted_by_reason(limits, action):
    policy = MutablePolicy()
    policy.deny.add(action)
    h = Harness(limits, policy=policy)
    if action == "read_session":
        with pytest.raises(ExecutionDenied):
            await h.run()
    else:
        result = await h.run()
        assert result.stop_reason == ("model_denied" if action == "call_model" else "final")
    assert not h.tool.calls
    assert h.facts("authorization")[-1].facts["allowed"] is False or action == "call_tool"


async def test_approval_reenters_current_policy_and_is_single_use(limits):
    policy = MutablePolicy()
    h = Harness(limits, manual=True, policy=policy)
    pending = await h.run()
    assert pending.stop_reason == "confirmation_required"
    assert pending.pending[0].arguments == {"count": 3, "label": "fabricated-default"}
    assert not h.tool.calls
    policy.deny.add("call_tool")
    answered = await h.answer(pending)
    assert answered is not None
    assert answered.stop_reason == "final"
    assert not h.tool.calls
    before = h.log.entries
    assert await h.answer(pending) is None
    assert h.log.entries == before


@pytest.mark.parametrize("approved", [True, False])
async def test_approval_approved_or_declined(limits, approved):
    h = Harness(limits, manual=True)
    pending = await h.run()
    result = await h.answer(pending, approved)
    assert result is not None
    assert result.stop_reason == "final"
    assert len(h.tool.calls) == int(approved)
    if not approved:
        assert h.facts("tool_call")[-1].facts["reason"] == "declined"


async def test_expired_approval_does_not_execute(limits):
    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    h = Harness(
        limits, manual=True, clock=lambda: now[0], limits=ExecutionLimits(approval_ttl_seconds=1)
    )
    pending = await h.run()
    now[0] += timedelta(seconds=2)
    await h.answer(pending)
    assert not h.tool.calls
    assert h.facts("confirmation")[-1].facts["state"] == "expired"


async def test_provider_mutation_cannot_change_approved_or_executed_arguments(limits):
    provider = DemoProvider()
    provider.mutate = True
    h = Harness(limits, manual=True, providers=(provider,))
    pending = await h.run()
    await h.answer(pending)
    assert h.tool.calls[0][1]["count"] == 3


@pytest.mark.parametrize("failure", ["refusal", "exception", "guard", "guard_exception"])
async def test_provider_and_guard_fail_closed(limits, failure):
    provider = DemoProvider()
    provider.fail = failure == "exception"
    provider.refusal = Refusal(reason="private content") if failure == "refusal" else None
    guard = DemoGuard(deny=failure == "guard", fail=failure == "guard_exception")
    h = Harness(limits, providers=(provider,), guards=(guard,))
    assert (await h.run()).stop_reason == "final"
    assert len(h.tool.calls) == int(failure.startswith("guard"))
    assert h.model.requests[-1].messages[-1].is_error
    assert "private content" not in str(h.log.entries)


async def test_changed_result_is_reguarded(limits):
    provider = DemoProvider()
    provider.redact = True
    guard = DemoGuard()
    h = Harness(limits, providers=(provider,), guards=(guard,))
    await h.run()
    assert guard.seen == ["Wrote 3", "redacted"]


@pytest.mark.parametrize("stage", ["intent", "result", "source"])
async def test_unknown_commits_stop_dependent_dispatch(limits, stage):
    h = Harness(limits)
    original = h.log.append

    async def append(entries):
        if any(
            (stage in {"intent", "result"} and e.key and e.key.startswith(stage + ":"))
            or (stage == "source" and e.call_manifest)
            for e in entries
        ):
            raise CommitUnknown("fabricated")
        return await original(entries)

    h.log.append = append
    with pytest.raises(CommitUnknown):
        await h.run()
    assert len(h.tool.calls) == int(stage == "result")
    assert len(h.model.requests) == 1


async def test_effect_failure_blocks_different_new_effectful_invocation(limits):
    h = Harness(limits, script=[proposal(), proposal(call_id="different"), final()])
    h.tool.failure = RuntimeError("fabricated private failure")
    assert (await h.run()).stop_reason == "final"
    assert len(h.tool.calls) == 1
    assert [e.facts.get("reason") for e in h.facts("tool_call")] == [
        None,
        "outcome_unknown",
        "effect_unresolved",
    ]


async def test_plain_answer_step_limit_and_outage(limits):
    h = Harness(limits, script=[final()])
    assert (await h.run()).stop_reason == "final"
    h = Harness(limits)
    h.spec = h.spec.model_copy(update={"max_steps": 1})
    assert (await h.run()).stop_reason == "step_limit"
    h = Harness(limits, script=[ModelUnavailable("unconfigured tier")])
    assert (await h.run()).stop_reason == "model_unavailable"


async def test_tool_timeout_leaves_unknown_effect(limits):
    h = Harness(limits, limits=ExecutionLimits(tool_timeout_seconds=0.001))

    async def run(ctx, args):
        await asyncio.Event().wait()
        return "unreachable"

    h.tool.run = run
    await h.run()
    assert h.facts("tool_call")[-1].facts["reason"] == "outcome_unknown"
