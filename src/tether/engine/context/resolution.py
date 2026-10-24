"""Resolve a single validated plan; fresh summaries use actual accepted journal positions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tether.context.validation import copied, validated_plan
from tether.types import ModelRequest
from tether.types.context import ContextInput, ContextPart, ContextPlan, ContextSummary

from .content import load
from .sources import charge
from .summary import SummaryCall

if TYPE_CHECKING:
    from .state import ContextState


async def resolve(
    step: ContextState, context: ContextInput, plan: ContextPlan, request: ModelRequest
) -> tuple[ContextInput, ContextPlan, tuple[ContextSummary, ...], dict[int, str]]:
    parts: list[ContextPart] = []
    selected: list[ContextSummary] = []
    texts: dict[int, str] = {}
    generated: list[ContextSummary] = []
    used = 0
    # Reserve the complete allowance before the first auxiliary call.
    validated_plan(context, plan, summary_allowance=step.config.max_summary_calls_per_step)
    for position, part in enumerate(plan.parts):
        if part.kind == "summarize":
            summary, text = await SummaryCall(step, context, part, position).execute(request)
            generated.append(summary)
            part = part.model_copy(update={"kind": "summary", "summary_index": summary.index})
        elif part.kind == "summary":
            summary = next(s for s in context.summaries if s.index == part.summary_index)
            text = await load(step, summary)
        else:
            parts.append(part)
            continue
        used = charge(text, step.config.max_source_bytes, used)
        selected.append(summary)
        texts[summary.index] = text
        parts.append(part)
    # This private materialization view advances only after actual accepted summary commits.
    # The strategy's original source watermark and the eventual use record do not change.
    context = copied(
        ContextInput,
        context.model_copy(
            update={
                "through_index": step.gov.log.entries[-1].index,
                "summaries": (*context.summaries, *generated),
            }
        ),
    )
    plan = validated_plan(context, plan.model_copy(update={"parts": tuple(parts)}))
    return context, plan, tuple(selected), texts
