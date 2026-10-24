"""Summary reuse never substitutes stale sources, profiles or authority."""

from typing import cast

import pytest
from test_agent_summaries import configuration, fixture, response, turn

from tether.context import SummarizingContext
from tether.engine.providers import BaseProvider
from tether.types import ModelResponse, ToolCall

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize(
    "change", ["scope", "principal", "config", "profile", "spec", "redaction", "strategy"]
)
async def test_summary_reuse_rechecks_current_bindings(limits, change):
    from tether.types.execution import Actor

    class Provider(BaseProvider):
        redact = False

        async def before_request(self, session, spec, messages):
            return tuple(
                m.model_copy(update={"content": "Redacted earlier answer"})
                if self.redact and m.content == "Fabricated old answer"
                else m
                for m in messages
            )

    provider = Provider()
    async with fixture(
        limits,
        providers=(provider,),
        script=[
            response("Fabricated old answer"),
            response("First summary"),
            response("Current answer"),
            response("Replacement summary"),
            response("Later answer"),
        ],
    ) as h:
        await turn(h, "first")
        await turn(h, "second")
        if change == "scope":
            # Fabricated managed scope; a production binding driver is separate work.
            state = h.root.index[h.session.id].state
            h.root.index[h.session.id].state = state.model_copy(
                update={"held": {"fabricated": ("value",)}}
            )
        elif change == "principal":
            h.actor = Actor(id=h.actor.id, principal="new-principal")
        elif change == "spec":
            h.spec = h.spec.model_copy(update={"instructions": "Changed fabricated instructions"})
        elif change == "redaction":
            provider.redact = True
        elif change == "strategy":
            h.engine.context.strategy.version = "2"
        else:
            old = h.engine.context.config
            if change == "config":
                new = old.model_copy(update={"max_summary_calls_per_step": 2})
            else:
                profile = old.limits["summary"].model_copy(update={"output_tokens": 257})
                new = old.model_copy(update={"limits": old.limits | {"summary": profile}})
            h.engine.context.config = new
        assert (await turn(h, "third"))[0].stop_reason == "final"
        assert [r.tags.purpose for r in h.model.requests].count("summary") == 2
        assert "Replacement summary" in repr(h.model.requests[-1].messages)
        if change == "redaction":
            assert "Fabricated old answer" not in repr(h.model.requests[-2].messages)


@pytest.mark.parametrize("failure", ["missing", "pending", "mismatched", "changed"])
async def test_unavailable_summary_never_silently_restores_raw_history(
    limits, failure, monkeypatch
):
    from tether.types.history import HistoryValue

    async with fixture(limits) as h:
        await turn(h, "first")
        _, entries = await turn(h, "second")
        compact = next(e for e in entries if e.kind == "compaction")
        original = h.provider.read

        async def read(refs, *, actor):
            values = await original(refs, actor=actor)
            if refs[0].kind != "summary":
                return values
            value = values[0]
            if failure in {"missing", "pending"}:
                return (
                    HistoryValue(
                        ref=refs[0], status="pending" if failure == "pending" else "unavailable"
                    ),
                )
            if failure == "mismatched":
                return (
                    value.model_copy(update={"ref": refs[0].model_copy(update={"key": "other"})}),
                )
            return (
                value.model_copy(
                    update={
                        "record": value.record.model_copy(update={"summary": "Changed summary"})
                    }
                ),
            )

        monkeypatch.setattr(h.provider, "read", read)
        result, after = await turn(h, "third")
        assert result.stop_reason == "context_unavailable"
        assert result.reason == (
            "history_pending"
            if failure == "pending"
            else "context_unavailable"
            if failure == "missing"
            else "context_incompatible"
        )
        assert len(h.model.requests) == 3
        assert sum(e.kind == "compaction" for e in after) == 1
        assert next(e for e in after if e.index == compact.index) == compact


@pytest.mark.parametrize("kind", ["tool", "oversize", "delta", "summary_fit", "main_fit"])
async def test_summary_failure_never_dispatches_tools_or_main(limits, kind):
    from tether.types import TextDelta

    summary = response("Generated summary")
    from tether.types import StreamItem

    script: list[StreamItem] = [response("Fabricated old answer"), summary]
    if kind == "tool":
        script[1] = ModelResponse(
            content="",
            finish_reason="tool_calls",
            tool_calls=(ToolCall(id="invented", name="write", arguments={"count": 1}),),
        )
    elif kind == "oversize":
        script[1] = response("x" * 9000)
    elif kind == "delta":
        script.insert(1, TextDelta(text="x" * 9000))
    values = configuration()
    if kind in {"summary_fit", "main_fit"}:

        class Estimator:
            async def estimate(self, request):
                from tether.types.context import TokenEstimate

                tokens = (
                    1_000_000
                    if (
                        request.tags.purpose == "summary"
                        if kind == "summary_fit"
                        else "Generated summary" in repr(request.messages)
                    )
                    else 0
                )
                return TokenEstimate(
                    tokens=tokens, method="fabricated", revision="1", quality="approximate"
                )

        values["estimator"] = Estimator()
    async with fixture(limits, settings=values, script=script) as h:
        await turn(h, "first")
        result, entries = await turn(h, "second")
        assert (result.stop_reason, result.reason) == (
            "context_unavailable",
            "context_exhausted" if kind.endswith("fit") else "context_failed",
        )
        assert len(h.model.requests) == (1 if kind == "summary_fit" else 2)
        assert not any(
            e.kind == "message" and e.turn_id == "second" and e.facts.get("role") == "assistant"
            for e in entries
        )


@pytest.mark.parametrize("users", [False, True])
async def test_opt_in_summarizing_planner_preserves_user_default_and_tool_groups(users):
    from test_context_foundation import context

    from tether.context.validation import indices, validated_plan

    value = context(window=30)
    strategy = SummarizingContext(summarize_user_messages=users)
    plan = validated_plan(value, await strategy.plan(value))
    proposed = [p for p in plan.parts if p.kind == "summarize"]
    assert len(proposed) == 1
    covered = set(indices(value, proposed[0].ranges))
    assert {4, 6}.issubset(covered)
    assert (0 in covered) is users
    assert 9 not in covered
    if not users:
        assert any(p.kind == "source" and 0 in indices(value, p.ranges) for p in plan.parts)


async def test_summarizing_builtin_passes_public_logical_suite():
    from contextlib import asynccontextmanager

    from tether.testing.conformance import context_strategy_suite

    @asynccontextmanager
    async def factory():
        yield SummarizingContext()

    report = await context_strategy_suite(factory)
    assert "context_input_immutable" in report.checks
    with pytest.raises(ValueError, match="context_summary_options"):
        SummarizingContext(summarize_user_messages=cast(bool, 1))


async def test_summary_text_is_bounded_before_provenance_commit(limits):
    async with fixture(
        limits,
        settings=configuration(max_source_bytes=4096),
        script=[response("Fabricated old answer"), response("x" * 5000)],
    ) as h:
        assert (await turn(h, "first"))[0].stop_reason == "final"
        result, entries = await turn(h, "second")
        assert (result.stop_reason, result.reason) == ("context_unavailable", "context_exhausted")
        assert len(h.model.requests) == 2
        assert not any(e.kind == "compaction" for e in entries)


async def test_ready_summary_cannot_replace_an_unavailable_original(limits, monkeypatch):
    from tether.types.history import HistoryValue

    async with fixture(limits) as h:
        await turn(h, "first")
        await turn(h, "second")
        original = h.provider.read

        async def read(refs, *, actor):
            values = await original(refs, actor=actor)
            if refs[0].kind == "message":
                return (HistoryValue(ref=refs[0], status="unavailable"),)
            return values

        monkeypatch.setattr(h.provider, "read", read)
        result, _ = await turn(h, "third")
        assert (result.stop_reason, result.reason) == ("context_unavailable", "history_unavailable")
        assert len(h.model.requests) == 3
