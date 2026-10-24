"""Independent C4 examples exercise strict snapshots, coverage and immutable materialization."""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import ValidationError

from tether.context import FullContext, TailContext
from tether.context.materialization import materialize, merged_parts
from tether.context.validation import validated_input, validated_plan
from tether.types import Message, ToolCall
from tether.types.canonical import digest
from tether.types.context import (
    CompactionRecord,
    ContextConfig,
    ContextError,
    ContextGroup,
    ContextInput,
    ContextPart,
    ContextPlan,
    ContextSource,
    ContextSummary,
    ContextUse,
    ModelLimits,
    SourceRange,
    TokenEstimate,
)
from tether.types.execution import HistoryRef

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def estimate(tokens):
    return TokenEstimate(tokens=tokens, method="fabricated", revision="1", quality="approximate")


def context(*, window=100, filtered=()):
    sources = tuple(
        ContextSource(index=i, message=m)
        for i, m in (
            (0, Message(role="user", content="Fabricated earlier user")),
            (2, Message(role="assistant", content="Fabricated earlier response")),
            (
                4,
                Message(
                    role="assistant",
                    content="",
                    tool_calls=(ToolCall(id="call", name="fixture", arguments={"count": 1}),),
                ),
            ),
            (6, Message(role="tool", tool_call_id="call", content="Fabricated result")),
            (9, Message(role="user", content="Fabricated current user")),
        )
    )
    return ContextInput(
        session_id="session",
        through_index=20,
        sources=sources,
        summaries=(),
        protected=frozenset({9}),
        limits=ModelLimits(
            profile_id="fixture",
            revision="1",
            context_window_tokens=window,
            output_tokens=10,
            safety_margin_tokens=5,
        ),
        source_view_digest=digest(["fabricated-source"]),
        fixed_estimate=estimate(2),
        groups=tuple(
            ContextGroup(indices=positions, estimate=estimate(tokens))
            for positions, tokens in (((0,), 3), ((2,), 7), ((4, 6), 10), ((9,), 3))
        ),
        filtered_indices=filtered,
    )


def part(
    start,
    end=None,
    *,
    kind: Literal["source", "summary", "summarize", "omit"] = "source",
    reason=None,
    summary=None,
):
    return ContextPart(
        kind=kind,
        ranges=(SourceRange(start=start, end=start if end is None else end),),
        reason=reason,
        summary_index=summary,
    )


def plan(*parts, name="fixture"):
    return ContextPlan(strategy_id=name, strategy_version="1", parts=parts)


def compaction():
    return CompactionRecord(
        preparation_ordinal=0,
        part_position=0,
        config_digest=digest("config"),
        summary_profile_id="summary",
        summary_profile_revision="1",
        summary_profile_digest=digest("summary-profile"),
        strategy_id="fixture",
        strategy_version="1",
        through_index=6,
        source_ranges=(SourceRange(start=4, end=6),),
        source_view_digest=digest("covered-view"),
        scope_digest=digest("scope"),
        spec_digest=digest("spec"),
        profile_id="fixture",
        profile_revision="1",
        summary_ref=HistoryRef(
            provider="fabricated",
            key="summary",
            sha256=digest("summary content"),
            owner="owner",
            session_id="session",
            kind="summary",
        ),
    )


@pytest.mark.parametrize(
    "field", ["context_window_tokens", "output_tokens", "safety_margin_tokens"]
)
@pytest.mark.parametrize("value", [True, "1", -1, float("inf"), float("nan")])
async def test_profile_counts_refuse_coercion_and_nonfinite(field, value):
    data = context().limits.model_dump() | {field: value}
    with pytest.raises(ValidationError):
        ModelLimits.model_validate(data)


@pytest.mark.parametrize("value", [True, float("inf"), float("nan"), 0])
async def test_deadline_is_strict_positive_finite(value):
    with pytest.raises(ValidationError):
        ContextConfig(limits={}, max_source_bytes=100, timeout_seconds=value)


async def test_output_margin_reserves_input_and_empty_tiers_stay_explicit():
    with pytest.raises(ValidationError):
        ModelLimits(
            profile_id="fixture",
            revision="1",
            context_window_tokens=10,
            output_tokens=8,
            safety_margin_tokens=2,
        )
    config = ContextConfig(limits={}, max_source_bytes=100, timeout_seconds=1)
    assert config.summary_identity_mode == "inherit"
    assert config.limits == {}


async def test_full_context_preserves_values_and_materialization_returns_copies():
    original = context()
    selected = await FullContext().plan(original)
    messages = materialize(original, selected)
    assert messages == tuple(s.message for s in original.sources)
    messages[2].tool_calls[0].arguments["count"] = 99
    assert original.sources[2].message.tool_calls[0].arguments["count"] == 1
    assert selected == await FullContext().plan(original)


async def test_tail_preserves_every_user_and_complete_tool_groups():
    original = context(window=25)
    selected = await TailContext().plan(original)
    assert [p.kind for p in selected.parts] == ["source", "omit", "omit", "source"]
    messages = materialize(original, selected)
    assert messages[0] == original.sources[0].message
    assert messages[-1] == original.sources[-1].message
    markers = [m for m in messages if m.role == "assistant"]
    assert all(not m.tool_calls and m.tool_call_id is None for m in markers)
    assert "4..6" in markers[1].content


@pytest.mark.parametrize("strategy", [FullContext, TailContext])
async def test_protected_user_overflow_is_explicit(strategy):
    with pytest.raises(ContextError, match="context_exhausted"):
        await strategy().plan(context(window=20))


@pytest.mark.parametrize(
    "parts",
    [
        (part(0, 2), part(4)),
        (part(0, 2), part(4), part(6, kind="omit", reason="budget"), part(9)),
        (part(0, 6), part(9, kind="omit", reason="budget")),
        (part(0, 2), part(4, 6, kind="omit", reason="provider_filter"), part(9)),
        (part(0, 3), part(4, 6), part(9)),
    ],
)
async def test_bad_coverage_split_groups_protection_and_forged_provider_filter(parts):
    with pytest.raises(ContextError):
        validated_plan(context(), plan(*parts))


async def test_filtered_ids_have_engine_markers_but_no_pre_redaction_content():
    original = context(filtered=(3, 8))
    selected = await FullContext().plan(original)
    merged = merged_parts(original, selected)
    assert [p.ranges[0].start for p in merged] == [0, 2, 3, 4, 8, 9]
    assert [p.reason for p in merged].count("provider_filter") == 2
    assert len(materialize(original, selected)) == len(original.sources) + 2
    with pytest.raises(ContextError):
        validated_plan(original, plan(part(0, 9)))


@pytest.mark.parametrize(
    "change",
    [
        {"filtered_indices": (2,)},
        {"filtered_indices": (7, 3)},
        {"filtered_indices": (5,)},
        {"protected": frozenset({8})},
        {"through_index": 8},
        {"filtered_indices": (True,)},
    ],
)
async def test_invalid_snapshot_and_unvalidated_typed_instances_refuse(change):
    with pytest.raises(ContextError):
        validated_input(context().model_copy(update=change))


async def test_pending_tool_group_requires_protection_and_duplicate_results_refuse():
    original = context()
    data = original.model_dump(mode="python")
    data["sources"] = tuple(s.model_dump(mode="python") for s in original.sources if s.index != 6)
    data["groups"] = tuple(
        ContextGroup(indices=(i,), estimate=estimate(1)).model_dump() for i in (0, 2, 4, 9)
    )
    pending = ContextInput.model_validate(data)
    with pytest.raises(ContextError):
        validated_input(pending)
    pending = pending.model_copy(update={"protected": frozenset({4, 9})})
    assert validated_input(pending).protected == frozenset({4, 9})
    bad = original.model_dump(mode="python")
    bad["sources"][3]["message"]["tool_call_id"] = "unknown"
    with pytest.raises(ContextError):
        validated_input(ContextInput.model_validate(bad))


async def test_summary_indices_are_real_positions_and_exact_covered_ranges():
    record = compaction()
    wrapper = ContextSummary(index=7, record=record)
    original = context().model_copy(update={"summaries": (wrapper,)})
    selected = plan(part(0, 2), part(4, 6, kind="summary", summary=7), part(9))
    assert validated_plan(original, selected) == selected
    messages = materialize(original, selected, summaries={7: "Fabricated generated summary"})
    assert "Generated summary" in messages[2].content
    assert not messages[2].tool_calls
    with pytest.raises(ContextError, match="context_unavailable"):
        materialize(original, selected)
    with pytest.raises(ValidationError):
        ContextSummary(index=6, record=record)
    with pytest.raises(ValidationError):
        ContextSummary(index=7, record=record.model_copy(update={"predecessor": 7}))


async def test_new_summaries_are_preflighted_and_not_materialized_before_commit():
    original = context()
    selected = plan(part(0, 2), part(4, 6, kind="summarize"), part(9))
    with pytest.raises(ContextError, match="context_exhausted"):
        validated_plan(original, selected, summary_allowance=0)
    assert validated_plan(original, selected, summary_allowance=1) == selected
    with pytest.raises(ContextError, match="context_invalid"):
        materialize(original, selected)
    with pytest.raises(ValidationError):
        ContextUse(
            preparation_ordinal=0,
            config_digest=digest("config"),
            through_index=20,
            parts=selected.parts,
            source_view_digest=original.source_view_digest,
            request_digest=digest("request"),
            profile_id="fixture",
            profile_revision="1",
            estimate=estimate(1),
            output_tokens=10,
            safety_margin_tokens=5,
        )


async def test_predecessor_must_exist_within_captured_snapshot():
    record = compaction()
    with pytest.raises(ValidationError):
        ContextSummary(index=9, record=record.model_copy(update={"predecessor": 8}))
    assert (
        ContextSummary(
            index=9, record=record.model_copy(update={"predecessor": 6})
        ).record.predecessor
        == 6
    )


@pytest.mark.parametrize("index", [9, 8])
async def test_summary_fact_cannot_occupy_original_message_position(index):
    original = context(filtered=(8,))
    forged = original.model_copy(
        update={"summaries": (ContextSummary(index=index, record=compaction()),)}
    )
    with pytest.raises(ContextError, match="context_invalid"):
        validated_input(forged)


@pytest.mark.parametrize("kind", ["tool", "attachment"])
async def test_nested_typed_message_unknown_fields_refuse_before_dump(kind):
    from tether.types import AttachmentRef

    if kind == "tool":
        forged = ToolCall(id="call", name="fixture", arguments={}).model_copy(
            update={"unexpected": "Fabricated"}
        )
        message = Message(role="assistant", content="", tool_calls=(forged,))
    else:
        forged = AttachmentRef(
            id="fixture", filename="fixture.txt", media_type="text/plain", size_bytes=1, read=True
        ).model_copy(update={"unexpected": "Fabricated"})
        message = Message(role="user", content="Fabricated", attachments=(forged,))
    with pytest.raises(ValidationError):
        ContextSource(index=0, message=message)
