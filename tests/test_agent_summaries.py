"""Actual Agent summary generation and compatible reuse through governed ports."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from tether.context.testing import fabricated_context
from tether.engine import Engine
from tether.history.jsonl import JsonlHistory
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ListAttemptRecorder, ScriptedModel
from tether.stores.memory import MemoryFiles, MemorySessionStore
from tether.types import ModelResponse, TokenUsage
from tether.types.context import ContextConfig, ContextPart, ContextPlan, SourceRange
from tether.types.execution import Actor, AgentSpec, Decision
from tether.types.storage import SessionInit

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def response(text="Fabricated answer", tokens=2):
    return ModelResponse(
        content=text,
        finish_reason="stop",
        usage=TokenUsage(input_tokens=tokens, output_tokens=tokens),
    )


class Strategy:
    id, version = "fabricated-summary", "1"

    async def plan(self, context):
        eligible = [
            s.index
            for s in context.sources
            if s.message.role == "assistant" and s.index not in context.protected
        ]
        target = eligible[0] if eligible else None
        parts = []
        for group in context.groups:
            span = (SourceRange(start=group.indices[0], end=group.indices[-1]),)
            existing = next((s for s in context.summaries if s.record.source_ranges == span), None)
            kind = (
                "source" if target not in group.indices else "summary" if existing else "summarize"
            )
            parts.append(
                ContextPart(
                    kind=kind,
                    ranges=span,
                    summary_index=existing.index
                    if kind == "summary" and existing is not None
                    else None,
                )
            )
        return ContextPlan(strategy_id=self.id, strategy_version=self.version, parts=tuple(parts))


def configuration(**updates):
    values = fabricated_context()
    old = values["context_config"]
    profile = old.limits["demo"].model_copy(
        update={"profile_id": "fabricated-summary", "output_tokens": 256}
    )
    settings = old.model_dump(mode="python") | {
        "limits": old.limits | {"summary": profile},
        "summary_model": "summary",
        "summary_identity_mode": "anonymous",
        **updates,
    }
    values["context_config"] = ContextConfig.model_validate(settings)
    return values


@asynccontextmanager
async def fixture(
    limits, *, script=None, actor=None, settings=None, custom=False, model=None, **kwargs
):
    actor = actor or Actor(id="fabricated-owner", principal="current-principal")
    files = MemoryFiles()
    root = await MemorySessionStore.open(files)
    store = root.for_actor(actor)
    provider = await JsonlHistory.open(files)
    session = await store.create(actor.id, SessionInit())
    model = model or ScriptedModel(
        script
        or [
            response("Fabricated old answer"),
            response("Generated summary", 7),
            response("Current answer"),
            response("Later answer"),
        ]
    )
    values = configuration() if settings is None else settings
    engine = Engine(
        **values,
        context=Strategy(),
        model=model,
        history=MessageHistoryAdapter(provider),
        history_provider=provider,
        model_limits=limits,
        attempt_factory=ListAttemptRecorder if custom else None,
        **kwargs,
    )
    spec = AgentSpec(
        id="demo", version="1", title="Demo", instructions="Fabricated instructions", model="demo"
    )
    h = SimpleNamespace(
        actor=actor,
        files=files,
        root=root,
        store=store,
        provider=provider,
        session=session,
        model=model,
        engine=engine,
        spec=spec,
    )
    try:
        yield h
    finally:
        assert await engine.aclose()
        assert await root.aclose()


async def turn(h, phase, text="Fabricated user request"):
    async with h.store.turn(h.session.id, h.actor.id, turn_id=phase) as log:
        result = await h.engine.run_turn(h.actor, log, text, spec=h.spec)
        return result, log.entries


@pytest.mark.parametrize("custom", [False, True])
async def test_actual_agent_summary_then_main_and_exact_reuse(limits, custom):
    async with fixture(limits, custom=custom) as h:
        assert (await turn(h, "first"))[0].stop_reason == "final"
        result, entries = await turn(h, "second")
        assert result.stop_reason == "final"
        assert [r.tags.purpose for r in h.model.requests] == ["answer", "summary", "answer"]
        auxiliary = h.model.requests[1]
        assert not auxiliary.tools
        assert auxiliary.max_output_tokens == 256
        assert auxiliary.identity_mode == "anonymous"
        assert auxiliary.tags.principal == "current-principal"
        assert all(not m.tool_calls and m.role == "user" for m in auxiliary.messages)
        assert "Fabricated old answer" in auxiliary.messages[0].content
        compact = next(e for e in entries if e.kind == "compaction")
        summary_call = next(
            e for e in entries if e.kind == "model_call" and e.facts["purpose"] == "summary"
        )
        assert (
            compact.facts["model_call_id"]
            == summary_call.facts["model_call_id"]
            == auxiliary.tags.model_call_id
        )
        use = next(e for e in entries if e.kind == "context_use" and e.turn_id == "second")
        assert compact.index < use.index
        assert any(p["summary_index"] == compact.index for p in use.facts["parts"])
        assert "Generated summary" in repr(h.model.requests[2].messages)
        assert "Fabricated old answer" not in repr(h.model.requests[2].messages)
        assert (await turn(h, "third"))[0].stop_reason == "final"
        assert [r.tags.purpose for r in h.model.requests].count("summary") == 1
        assert "Generated summary" in repr(h.model.requests[3].messages)
        group = await h.store.lookup(
            h.session.id, h.actor.id, kind="model_call", value=auxiliary.tags.model_call_id
        )
        assert {e.kind for e in group} == (
            {"model_call", "compaction"}
            if custom
            else {"model_call", "compaction", "model_attempt", "model_receipt"}
        )


@pytest.mark.parametrize("mode", ["inherit", "caller", "application", "anonymous"])
async def test_summary_identity_is_explicit_and_never_substituted(limits, mode):
    class Credential:
        async def authorization(self):
            return "Fabricated " + "credential"

    actor = Actor(id="fabricated-owner", principal="principal", credential=Credential())
    async with fixture(
        limits, actor=actor, settings=configuration(summary_identity_mode=mode)
    ) as h:
        await turn(h, "first")
        assert (await turn(h, "second"))[0].stop_reason == "final"
        assert h.model.requests[1].identity_mode == ("caller" if mode == "inherit" else mode)


@pytest.mark.parametrize("fault", ["missing_credential", "unconfigured", "allowance", "policy"])
async def test_summary_refusal_cannot_send_main(limits, fault):
    class Policy:
        async def authorize(self, request):
            return Decision(
                allowed=not (fault == "policy" and request.purpose == "summary"), reason="denied"
            )

    updates: dict[str, object] = (
        {"summary_identity_mode": "caller"} if fault == "missing_credential" else {}
    )
    if fault == "unconfigured":
        updates["summary_model"] = None
    if fault == "allowance":
        updates["max_summary_calls_per_step"] = 0
    async with fixture(limits, settings=configuration(**updates), policy=Policy()) as h:
        await turn(h, "first")
        result, entries = await turn(h, "second")
        assert result.stop_reason == "context_unavailable"
        assert (
            result.reason
            == {
                "missing_credential": "context_denied",
                "unconfigured": "context_unconfigured",
                "allowance": "context_exhausted",
                "policy": "context_denied",
            }[fault]
        )
        assert len(h.model.requests) == 1
        assert not any(
            e.kind in {"compaction", "context_use"} and e.turn_id == "second" for e in entries
        )


@pytest.mark.parametrize(
    "bad",
    [
        ModelResponse(content="", finish_reason="stop"),
        ModelResponse(content="Partial summary", finish_reason="length"),
        ModelResponse(content="Refused", finish_reason="filtered"),
    ],
)
async def test_bad_summary_terminal_preserves_originals_and_charges_usage(limits, bad):
    async with fixture(
        limits,
        script=[
            response("Original answer"),
            bad.model_copy(update={"usage": TokenUsage(input_tokens=3, output_tokens=3)}),
        ],
    ) as h:
        await turn(h, "first")
        result, entries = await turn(h, "second")
        assert (result.stop_reason, result.reason) == ("context_unavailable", "context_failed")
        assert len(h.model.requests) == 2
        assert not any(e.kind == "compaction" for e in entries)
        calls = [e for e in entries if e.kind == "model_call" and e.facts["purpose"] == "summary"]
        assert len(calls) == 1
        assert calls[0].facts["status"] == "error"


async def test_mutating_summary_put_cannot_replace_expected_content(limits, monkeypatch):
    async with fixture(limits) as h:
        await turn(h, "first")
        original = h.provider.put

        async def mutate(**values):
            record = values["record"]
            if record.kind == "summary":
                object.__setattr__(record, "summary", "Provider replacement text")
            return await original(**values)

        monkeypatch.setattr(h.provider, "put", mutate)
        result, entries = await turn(h, "second")
        assert (result.stop_reason, result.reason) == (
            "context_unavailable",
            "context_incompatible",
        )
        assert not any(e.kind == "compaction" for e in entries)
        assert len(h.model.requests) == 2


async def test_known_summary_commit_cannot_gain_a_contradictory_error_terminal(limits, monkeypatch):
    async with fixture(limits) as h:
        await turn(h, "first")
        saved = h.engine.context.config
        async with h.store.turn(h.session.id, h.actor.id, turn_id="second") as log:
            original = log.append

            async def change_after_commit(values):
                accepted = await original(values)
                if any(e.kind == "compaction" for e in values):
                    h.engine.context.config = saved.model_copy(update={"timeout_seconds": 31.0})
                return accepted

            monkeypatch.setattr(log, "append", change_after_commit)
            result = await h.engine.run_turn(h.actor, log, "Fabricated", spec=h.spec)
            entries = log.entries
        assert (result.stop_reason, result.reason) == (
            "context_unavailable",
            "context_incompatible",
        )
        calls = [e for e in entries if e.kind == "model_call" and e.facts["purpose"] == "summary"]
        assert len(calls) == 1
        assert calls[0].facts["status"] == "ok"
        assert any(e.kind == "compaction" for e in entries)
        h.engine.context.config = saved
        assert (await turn(h, "third"))[0].stop_reason == "final"
        assert len(h.model.requests) == 3


async def test_known_summary_cost_is_visible_before_main_authorization(limits):
    observed = []

    class Policy:
        async def authorize(self, request):
            if request.action == "call_model" and request.purpose == "answer":
                observed.append(request.usage.model_copy(deep=True))
            deny = (
                request.action == "call_model"
                and request.purpose == "answer"
                and request.usage.summary_calls
            )
            return Decision(allowed=not deny, reason="budget_exceeded" if deny else "allowed")

    async with fixture(limits, policy=Policy()) as h:
        await turn(h, "first")
        result, entries = await turn(h, "second")
        assert result.stop_reason == "model_denied"
        assert len(h.model.requests) == 2
        assert observed[-1].summary_calls == 1
        assert observed[-1].input_tokens == 7
        assert observed[-1].steps == 0
        assert any(e.kind == "compaction" for e in entries)


async def test_summary_exception_text_is_not_a_turn_reason(limits):
    async with fixture(
        limits, script=[response(), RuntimeError("provider exception content marker")]
    ) as h:
        await turn(h, "first")
        result, entries = await turn(h, "second")
        assert (result.stop_reason, result.reason) == ("context_unavailable", "context_failed")
        assert "provider exception content marker" not in repr(entries)
        assert len(h.model.requests) == 2


async def test_folding_prior_summary_consumes_coverage_once(limits):
    from tether.context.validation import indices

    class Fold(Strategy):
        async def plan(self, context):
            initial = await super().plan(context)
            if not context.summaries:
                return initial
            prior = context.summaries[-1]
            begin = prior.record.source_ranges[0].start
            selected = [
                s.index
                for s in context.sources
                if s.index >= begin and s.index not in context.protected
            ]
            if not selected:
                return initial
            parts = tuple(p for p in initial.parts if indices(context, p.ranges)[-1] < begin)
            folded = ContextPart(
                kind="summarize",
                ranges=(SourceRange(start=begin, end=selected[-1]),),
                summary_index=prior.index,
            )
            final = tuple(p for p in initial.parts if indices(context, p.ranges)[0] > selected[-1])
            return initial.model_copy(update={"parts": (*parts, folded, *final)})

    async with fixture(
        limits,
        script=[
            response("Fabricated old answer"),
            response("Prior summary"),
            response("Next original answer"),
            response("Folded summary"),
            response("Final answer"),
        ],
    ) as h:
        h.engine.context.strategy = Fold()
        await turn(h, "first")
        _, before = await turn(h, "second")
        assert (await turn(h, "third"))[0].stop_reason == "final"
        auxiliary = h.model.requests[-2]
        assert auxiliary.tags.purpose == "summary"
        assert "Prior summary" in auxiliary.messages[0].content
        assert "Next original answer" in auxiliary.messages[0].content
        assert "Fabricated old answer" not in auxiliary.messages[0].content
        async with h.store.turn(h.session.id, h.actor.id, turn_id="inspect") as log:
            summaries = [e for e in log.entries if e.kind == "compaction"]
            assert len(summaries) == 2
            assert summaries[-1].facts["predecessor"] == summaries[0].index
            assert summaries[0] == next(e for e in before if e.kind == "compaction")


@pytest.mark.parametrize("mode", ["inherit", "caller", "application", "anonymous"])
async def test_summary_refreshes_live_credential_after_policy_await(limits, mode):
    seen = []

    class Credential:
        async def authorization(self):
            return "Fabricated " + "credential"

    class Capture(ScriptedModel):
        async def stream(self, request):
            if request.tags.purpose == "summary":
                seen.append(request.credential)
            async for item in super().stream(request):
                yield item

    old, current = Credential(), Credential()
    actor = Actor(id="fabricated-owner", credential=old)

    class Policy:
        async def authorize(self, request):
            if request.purpose == "summary":
                h.engine._runs[h.session.id].gov.actor = actor.model_copy(
                    update={"credential": current}
                )
            return Decision(allowed=True, reason="allowed")

    model = Capture([response(), response("Generated summary"), response()])
    async with fixture(
        limits,
        actor=actor,
        model=model,
        policy=Policy(),
        settings=configuration(summary_identity_mode=mode),
    ) as h:
        await turn(h, "first")
        assert (await turn(h, "second"))[0].stop_reason == "final"
        assert seen == [current if mode in {"inherit", "caller"} else None]


async def test_actual_summarizing_builtin_generates_then_reuses_with_user_sources(limits):
    from tether.context import SummarizingContext
    from tether.types.context import TokenEstimate

    class Estimator:
        async def estimate(self, request):
            return TokenEstimate(
                tokens=2
                + sum(100 if m.content == "Fabricated old answer" else 1 for m in request.messages),
                method="fabricated-weighted",
                revision="1",
                quality="approximate",
            )

    values = configuration()
    config = values["context_config"]
    main = config.limits["demo"].model_copy(
        update={"context_window_tokens": 45, "output_tokens": 10, "safety_margin_tokens": 5}
    )
    values["context_config"] = config.model_copy(update={"limits": config.limits | {"demo": main}})
    values["estimator"] = Estimator()
    async with fixture(limits, settings=values) as h:
        h.engine.context.strategy = SummarizingContext()
        await turn(h, "first", "Fabricated first input")
        assert (await turn(h, "second", "Fabricated second input"))[0].stop_reason == "final"
        assert (await turn(h, "third", "Fabricated third input"))[0].stop_reason == "final"
        assert [r.tags.purpose for r in h.model.requests] == [
            "answer",
            "summary",
            "answer",
            "answer",
        ]
        final = h.model.requests[-1].messages
        assert all(
            any(m.content == text for m in final)
            for text in (
                "Fabricated first input",
                "Fabricated second input",
                "Fabricated third input",
            )
        )
        assert "Fabricated old answer" not in repr(final)
