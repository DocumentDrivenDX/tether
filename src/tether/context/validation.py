"""Pure source/plan validation confers no storage, policy or execution authority."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from typing import TypeVar

from tether.types.context import (
    ContextError,
    ContextInput,
    ContextPart,
    ContextPlan,
    ContextValue,
    SourceRange,
)

T = TypeVar("T", bound=ContextValue)


def copied(model: type[T], value: T) -> T:
    try:
        if set(value.__dict__) != set(model.model_fields):
            raise ValueError("shape")
        checked = model.model_validate(value)
        return model.model_validate(checked.model_dump(mode="python", warnings=False)).model_copy(
            deep=True
        )
    except (ValueError, TypeError, AttributeError):
        raise ContextError("context_invalid") from None


def indices(context: ContextInput, ranges: tuple[SourceRange, ...]) -> tuple[int, ...]:
    positions = tuple(s.index for s in context.sources)
    result: list[int] = []
    for span in ranges:
        if span.start not in positions or span.end not in positions:
            raise ContextError("context_invalid")
        if any(span.start <= f <= span.end for f in context.filtered_indices):
            raise ContextError("context_invalid")
        result.extend(
            positions[bisect_left(positions, span.start) : bisect_right(positions, span.end)]
        )
    return tuple(result)


def _group_messages(context: ContextInput) -> None:
    messages = {s.index: s.message for s in context.sources}
    for group in context.groups:
        pending: set[str] = set()
        for position in group.indices:
            message = messages[position]
            if message.role == "tool":
                if message.tool_calls or message.tool_call_id not in pending:
                    raise ContextError("context_invalid")
                pending.remove(message.tool_call_id)
            elif message.tool_call_id is not None:
                raise ContextError("context_invalid")
            elif message.tool_calls:
                ids = tuple(call.id for call in message.tool_calls)
                if message.role != "assistant" or pending or len(set(ids)) != len(ids):
                    raise ContextError("context_invalid")
                pending.update(ids)
        if pending and not set(group.indices).issubset(context.protected):
            raise ContextError("context_invalid")


def validated_input(value: ContextInput) -> ContextInput:
    context = copied(ContextInput, value)
    _group_messages(context)
    return context


def _summary(
    context: ContextInput, plan: ContextPlan, part: ContextPart, covered: tuple[int, ...]
) -> None:
    if part.summary_index is None:
        return
    chosen = next((s for s in context.summaries if s.index == part.summary_index), None)
    if chosen is None:
        raise ContextError("context_incompatible")
    record = chosen.record
    if (
        record.strategy_id,
        record.strategy_version,
        record.profile_id,
        record.profile_revision,
    ) != (
        plan.strategy_id,
        plan.strategy_version,
        context.limits.profile_id,
        context.limits.revision,
    ):
        raise ContextError("context_incompatible")
    prior = indices(context, record.source_ranges)
    if part.kind == "summary" and prior != covered:
        raise ContextError("context_invalid")
    if part.kind == "summarize" and not set(prior).issubset(covered):
        raise ContextError("context_invalid")


def validated_plan(
    context: ContextInput,
    value: ContextPlan,
    *,
    summary_allowance: int | None = None,
) -> ContextPlan:
    context, plan = validated_input(context), copied(ContextPlan, value)
    owners: dict[int, int] = {}
    covered: list[int] = []
    for number, part in enumerate(plan.parts):
        if any(part.ranges[0].start <= f <= part.ranges[-1].end for f in context.filtered_indices):
            raise ContextError("context_invalid")
        positions = indices(context, part.ranges)
        if part.reason == "provider_filter" or (
            set(positions) & context.protected and part.kind != "source"
        ):
            raise ContextError("context_invalid")
        _summary(context, plan, part, positions)
        covered.extend(positions)
        owners.update((i, number) for i in positions)
    if tuple(covered) != tuple(s.index for s in context.sources):
        raise ContextError("context_invalid")
    for group in context.groups:
        selected = {owners[i] for i in group.indices}
        if len(selected) != 1 and any(plan.parts[p].kind != "source" for p in selected):
            raise ContextError("context_invalid")
    if summary_allowance is not None:
        if type(summary_allowance) is not int or summary_allowance < 0:
            raise ValueError("context_summary_allowance")
        if sum(p.kind == "summarize" for p in plan.parts) > summary_allowance:
            raise ContextError("context_exhausted")
    return plan
