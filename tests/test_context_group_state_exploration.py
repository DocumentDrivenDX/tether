"""Generated history cuts preserve tool pairs and account for every source."""

import asyncio

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tether.context import FullContext, TailContext
from tether.context.materialization import materialize
from tether.context.validation import indices, validated_plan
from tether.types import Message, ToolCall
from tether.types.canonical import digest
from tether.types.context import (
    ContextError,
    ContextGroup,
    ContextInput,
    ContextPart,
    ContextPlan,
    ContextSource,
    ModelLimits,
    SourceRange,
    TokenEstimate,
)


def _estimate(tokens: int) -> TokenEstimate:
    return TokenEstimate(tokens=tokens, method="fabricated", revision="1", quality="exact")


def _history(pairs: int, input_budget: int) -> ContextInput:
    sources = [ContextSource(index=0, message=Message(role="user", content="Fabricated start"))]
    groups = [ContextGroup(indices=(0,), estimate=_estimate(2))]
    for number in range(pairs):
        call_index = 3 * number + 2
        call_id = f"fabricated-call-{number}"
        sources.extend(
            (
                ContextSource(
                    index=call_index,
                    message=Message(
                        role="assistant",
                        content="",
                        tool_calls=(
                            ToolCall(
                                id=call_id,
                                name="fabricated_lookup",
                                arguments={"item": number},
                            ),
                        ),
                    ),
                ),
                ContextSource(
                    index=call_index + 1,
                    message=Message(role="tool", tool_call_id=call_id, content=f"result-{number}"),
                ),
            )
        )
        groups.append(ContextGroup(indices=(call_index, call_index + 1), estimate=_estimate(2)))
    current = 3 * pairs + 2
    sources.append(
        ContextSource(index=current, message=Message(role="user", content="Fabricated now"))
    )
    groups.append(ContextGroup(indices=(current,), estimate=_estimate(2)))
    return ContextInput(
        session_id="fabricated-session",
        through_index=current,
        sources=tuple(sources),
        summaries=(),
        protected=frozenset({current}),
        limits=ModelLimits(
            profile_id="fabricated-profile",
            revision="1",
            context_window_tokens=input_budget + 15,
            output_tokens=10,
            safety_margin_tokens=5,
        ),
        source_view_digest=digest("fabricated-history"),
        fixed_estimate=_estimate(1),
        groups=tuple(groups),
    )


@settings(max_examples=30, deadline=None)
@given(pairs=st.integers(1, 5), input_budget=st.integers(5, 18))
def test_every_generated_tool_group_cut_is_atomic_and_accounted(
    pairs: int, input_budget: int
) -> None:
    original = _history(pairs, input_budget)
    before = original.model_dump(mode="json")
    selected = asyncio.run(TailContext().plan(original))
    assert validated_plan(original, selected) == selected
    assert [i for part in selected.parts for i in indices(original, part.ranges)] == [
        source.index for source in original.sources
    ]
    assert selected.parts[0].kind == selected.parts[-1].kind == "source"

    for number in range(pairs):
        part = selected.parts[number + 1]
        assert part.kind in {"source", "omit"}
        first, second = original.groups[number + 1].indices
        assert indices(original, part.ranges) == (first, second)
        split = ContextPlan(
            strategy_id="fabricated-cut",
            strategy_version="1",
            parts=(
                *selected.parts[: number + 1],
                ContextPart(kind="source", ranges=(SourceRange(start=first, end=first),)),
                ContextPart(
                    kind="omit",
                    ranges=(SourceRange(start=second, end=second),),
                    reason="budget",
                ),
                *selected.parts[number + 2 :],
            ),
        )
        with pytest.raises(ContextError, match="context_invalid"):
            validated_plan(original, split)

    visible = materialize(original, selected)
    for number in range(pairs):
        call_id = f"fabricated-call-{number}"
        has_call = any(any(call.id == call_id for call in m.tool_calls) for m in visible)
        has_result = any(m.tool_call_id == call_id for m in visible)
        assert has_call == has_result
        if not has_call:
            first, second = original.groups[number + 1].indices
            assert any(f"Omitted source entries {first}..{second}" in m.content for m in visible)
    assert original.model_dump(mode="json") == before

    if 1 + 2 * len(original.groups) <= input_budget:
        assert materialize(original, asyncio.run(FullContext().plan(original))) == tuple(
            source.message for source in original.sources
        )
