"""Actual Agent context projections preserve originals and require accepted measured uses."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from test_gated_execution import DemoTool, final, proposal

from tether.context import TailContext
from tether.context.testing import FixedEstimator, fabricated_context
from tether.engine import Engine
from tether.engine.context.step import request_digest
from tether.engine.providers import BaseProvider
from tether.models.testing import ScriptedModel
from tether.testing.execution import MemoryHistory, MemoryTurnLog
from tether.types import Message
from tether.types.context import ModelLimits
from tether.types.execution import Actor, AgentSpec, Answer, SessionState

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@asynccontextmanager
async def fixture(limits, **options):
    settings = options.copy()
    defaults = fabricated_context()
    for key, value in defaults.items():
        settings.setdefault(key, value)
    actor = Actor(id="fabricated-owner")
    history, log = MemoryHistory(), MemoryTurnLog(SessionState(id="session", owner=actor.id))
    tool = DemoTool()
    model = settings.pop("model", ScriptedModel([final()]))
    engine = Engine(model=model, history=history, model_limits=limits, tools=(tool,), **settings)
    spec = AgentSpec(
        id="demo",
        version="1",
        title="Demo",
        instructions="Fabricated instructions",
        model="demo",
        tools=(tool.name,),
    )
    value = SimpleNamespace(
        actor=actor, history=history, log=log, tool=tool, model=model, engine=engine, spec=spec
    )
    try:
        yield value
    finally:
        assert await engine.aclose()


async def run(value, text="Fabricated request"):
    return await value.engine.run_turn(value.actor, value.log, text, spec=value.spec)


async def test_actual_agent_accepts_context_before_attempt_and_preserves_originals(limits):
    async with fixture(limits) as h:
        result = await run(h)
        assert result.stop_reason == "final"
        prepared, use, attempt = [
            next(e for e in h.log.entries if e.kind == kind)
            for kind in ("context_preparation", "context_use", "model_attempt")
        ]
        assert prepared.index < use.index < attempt.index
        assert prepared.key == "context:prepare:0"
        assert use.key == "context:use:0"
        assert use.facts["request_digest"] == request_digest(h.model.requests[0])
        assert use.facts["through_index"] < prepared.index
        assert h.model.requests[0].max_output_tokens == 1024
        assert "Fabricated request" not in str([e.model_dump() for e in h.log.entries])
        assert len([e for e in h.log.entries if e.kind == "message"]) == 2


@pytest.mark.parametrize("missing", ["context_config", "estimator", "tier"])
async def test_missing_explicit_configuration_never_enters_model_port(limits, missing):
    options = (
        {missing: None}
        if missing != "tier"
        else {
            "context_config": fabricated_context()["context_config"].model_copy(
                update={"limits": {}}
            )
        }
    )
    async with fixture(limits, **options) as h:
        result = await run(h)
        assert (result.stop_reason, result.reason) == (
            "context_unavailable",
            "context_unconfigured",
        )
        assert not h.model.requests
        assert not [e for e in h.log.entries if e.kind == "context_use"]


@pytest.mark.parametrize("tokens", [80, 81])
async def test_exact_final_fit_and_one_token_overflow(limits, tokens):
    config = fabricated_context()["context_config"].model_copy(
        update={
            "limits": {
                "demo": ModelLimits(
                    profile_id="fabricated",
                    revision="1",
                    context_window_tokens=100,
                    output_tokens=10,
                    safety_margin_tokens=10,
                )
            }
        }
    )
    async with fixture(limits, context_config=config, estimator=FixedEstimator(tokens)) as h:
        result = await run(h)
        assert result.stop_reason == ("final" if tokens == 80 else "context_unavailable")
        assert len(h.model.requests) == (1 if tokens == 80 else 0)
        if tokens == 80:
            assert h.model.requests[0].max_output_tokens == 10
        else:
            assert result.reason == "context_exhausted"


async def test_multistep_agent_keeps_full_sdk_originals_and_dense_preparation_identity(limits):
    async with fixture(limits, model=ScriptedModel([proposal(), final()])) as h:
        result = await run(h)
        assert result.stop_reason == "final"
        assert len(h.tool.calls) == 1
        assert [e.facts["ordinal"] for e in h.log.entries if e.kind == "context_preparation"] == [
            0,
            1,
        ]
        assert len(h.model.requests[1].messages) == 3
        source = h.model.requests[1].messages[1]
        assert source.tool_calls[0].arguments["count"] == "3"
        assert h.tool.calls[0][1]["count"] == 3


async def test_cached_approval_continuation_allocates_a_fresh_preparation(limits):
    async with fixture(limits, model=ScriptedModel([proposal(), final()])) as h:
        h.log.session = h.log.session.model_copy(update={"approval": "manual"})
        waiting = await run(h)
        assert waiting.stop_reason == "confirmation_required"
        result = await h.engine.answer(
            h.actor, h.log, waiting.pending[0].invocation_id, Answer(approved=True)
        )
        assert result.stop_reason == "final"
        assert [e.facts["ordinal"] for e in h.log.entries if e.kind == "context_preparation"] == [
            0,
            1,
        ]
        assert h.model.requests[1].messages[1].tool_calls
        assert h.model.requests[1].messages[2].role == "tool"


async def test_successive_provider_identity_filters_keep_exact_original_positions(limits):
    class First(BaseProvider):
        id = "first"

        async def before_request(self, session, spec, messages):
            return tuple(m for m in messages if m.role != "assistant")

    class Second(BaseProvider):
        id = "second"

        async def before_request(self, session, spec, messages):
            return messages[-1:]

    async with fixture(
        limits, model=ScriptedModel([final(), final()]), providers=(First(), Second())
    ) as h:
        assert (await run(h, "Fabricated earlier request")).stop_reason == "final"
        first_sources = tuple(e.index for e in h.log.entries if e.kind == "message")
        assert (await run(h, "Fabricated later request")).stop_reason == "final"
        request = h.model.requests[1]
        assert len(request.messages) == 3
        assert [m.content for m in request.messages[:2]] == [
            f"[Omitted source entries {i}; reason: provider_filter]" for i in first_sources
        ]
        use = [e for e in h.log.entries if e.kind == "context_use"][-1]
        assert [p["ranges"][0]["start"] for p in use.facts["parts"][:2]] == list(first_sources)
        originals = [
            await h.history.read(e.history_ref, actor=h.actor, session_id=h.log.session.id)
            for e in h.log.entries
            if e.kind == "message"
        ]
        assert originals[0].content == "Fabricated earlier request"
        assert request.messages[-1].content == "Fabricated later request"


@pytest.mark.parametrize("identity", [False, True])
async def test_fresh_ambiguous_provider_outputs_refuse_but_exact_identity_is_usable(
    limits, identity
):
    class Filter(BaseProvider):
        async def before_request(self, session, spec, messages):
            if len(messages) == 1:
                return messages
            return (messages[-1],) if identity else (Message(role="user", content="Redacted"),)

    async with fixture(limits, model=ScriptedModel([final(), final()]), providers=(Filter(),)) as h:
        assert (await run(h)).stop_reason == "final"
        result = await run(h, "Fabricated current request")
        assert result.stop_reason == ("final" if identity else "context_unavailable")
        assert len(h.model.requests) == (2 if identity else 1)
        if not identity:
            assert result.reason == "context_invalid"


async def test_provider_cannot_filter_current_user(limits):
    class Filter(BaseProvider):
        async def before_request(self, session, spec, messages):
            return ()

    async with fixture(limits, providers=(Filter(),)) as h:
        result = await run(h)
        assert result.reason == "context_invalid"
        assert not h.model.requests


async def test_large_fixed_instructions_are_bounded_before_estimator_or_egress(limits):
    config = fabricated_context()["context_config"].model_copy(update={"max_source_bytes": 2000})
    estimator = FixedEstimator(0)
    async with fixture(limits, context_config=config, estimator=estimator) as h:
        h.spec = h.spec.model_copy(update={"instructions": "Fabricated " * 1000})
        result = await run(h)
        assert result.reason == "context_exhausted"
        assert not estimator.requests
        assert not h.model.requests


async def test_tail_can_omit_completed_old_groups_without_altering_agent_history(limits):
    from tether.types.context import TokenEstimate

    class Weighted:
        async def estimate(self, request):
            tokens = 1 + sum(
                2 if m.role == "user" else 1 if m.content.startswith("[Omitted") else 10
                for m in request.messages
            )
            return TokenEstimate(
                tokens=tokens, method="fabricated-weights", revision="1", quality="approximate"
            )

    config = fabricated_context()["context_config"].model_copy(
        update={
            "limits": {
                "demo": ModelLimits(
                    profile_id="fabricated",
                    revision="1",
                    context_window_tokens=41,
                    output_tokens=10,
                    safety_margin_tokens=5,
                )
            }
        }
    )
    async with fixture(
        limits,
        model=ScriptedModel([final(), proposal(), final()]),
        context=TailContext(),
        context_config=config,
        estimator=Weighted(),
    ) as h:
        assert (await run(h)).stop_reason == "final"
        old_answer = next(
            e for e in h.log.entries if e.kind == "message" and e.facts.get("role") == "assistant"
        )
        assert (await run(h, "Fabricated new input")).stop_reason == "final"
        assert len(h.tool.calls) == 1
        request = h.model.requests[-1]
        assert (
            request.messages[1].content
            == f"[Omitted source entries {old_answer.index}; reason: budget]"
        )
        assert (
            await h.history.read(old_answer.history_ref, actor=h.actor, session_id=h.log.session.id)
        ).content == "Fabricated done"
        use = [e for e in h.log.entries if e.kind == "context_use"][-1]
        assert use.facts["estimate"]["tokens"] == 26


async def test_provider_cannot_remove_one_member_of_a_completed_old_tool_group(limits):
    class Partial(BaseProvider):
        async def before_request(self, session, spec, messages):
            if any(m.content == "Fabricated second input" for m in messages):
                return tuple(m for m in messages if m.role != "tool")
            return messages

    async with fixture(
        limits, model=ScriptedModel([proposal(), final(), final()]), providers=(Partial(),)
    ) as h:
        assert (await run(h)).stop_reason == "final"
        result = await run(h, "Fabricated second input")
        assert (result.stop_reason, result.reason) == ("context_unavailable", "context_invalid")
        assert len(h.model.requests) == 2
        assert len(h.tool.calls) == 1


async def test_sdk_unsupported_attachments_refuse_before_model_egress(limits):
    from tether.types import AttachmentRef
    from tether.types.execution import TurnStart

    estimator = FixedEstimator(0)
    async with fixture(limits, estimator=estimator) as h:
        start = TurnStart(
            turn_id="attached",
            attachments=(
                AttachmentRef(
                    id="fabricated",
                    filename="fixture.png",
                    media_type="image/png",
                    size_bytes=32,
                    read=True,
                ),
            ),
        )
        result = await h.engine.run_turn(h.actor, h.log, "Fabricated", spec=h.spec, start=start)
        assert (result.stop_reason, result.reason) == ("model_unavailable", "unsupported_content")
        assert not h.model.requests
        assert not estimator.requests
