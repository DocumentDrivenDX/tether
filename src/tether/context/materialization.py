"""Deterministic, non-executable markers preserve visible and filtered source coverage."""

from collections.abc import Mapping

from tether.types import Message
from tether.types.context import ContextError, ContextInput, ContextPart, ContextPlan, SourceRange

from .validation import copied, indices, validated_input, validated_plan


def provider_omissions(context: ContextInput) -> tuple[ContextPart, ...]:
    return tuple(
        ContextPart(kind="omit", ranges=(SourceRange(start=i, end=i),), reason="provider_filter")
        for i in context.filtered_indices
    )


def merged_parts(context: ContextInput, plan: ContextPlan) -> tuple[ContextPart, ...]:
    context, plan = validated_input(context), validated_plan(context, plan)
    parts = tuple(
        sorted((*plan.parts, *provider_omissions(context)), key=lambda p: p.ranges[0].start)
    )
    return validated_merged(context, plan, parts)


def validated_merged(
    context: ContextInput, plan: ContextPlan, parts: tuple[ContextPart, ...]
) -> tuple[ContextPart, ...]:
    """Validate the engine-owned filtered markers separately from the strategy plan."""
    context, plan = validated_input(context), validated_plan(context, plan)
    parts = tuple(copied(ContextPart, part) for part in parts)
    expected = tuple(
        sorted((*plan.parts, *provider_omissions(context)), key=lambda p: p.ranges[0].start)
    )
    if parts != expected:
        raise ContextError("context_invalid")
    return parts


def _ranges(part: ContextPart) -> str:
    return ",".join(
        str(r.start) if r.start == r.end else f"{r.start}..{r.end}" for r in part.ranges
    )


def materialize(
    context: ContextInput,
    plan: ContextPlan,
    *,
    summaries: Mapping[int, str] | None = None,
) -> tuple[Message, ...]:
    context = validated_input(context)
    messages = {s.index: s.message for s in context.sources}
    result: list[Message] = []
    for part in merged_parts(context, plan):
        if part.kind == "source":
            result.extend(messages[i].model_copy(deep=True) for i in indices(context, part.ranges))
        elif part.kind == "omit":
            result.append(
                Message(
                    role="assistant",
                    content=f"[Omitted source entries {_ranges(part)}; reason: {part.reason}]",
                )
            )
        elif part.kind == "summary":
            text = (
                summaries.get(part.summary_index)
                if summaries is not None and part.summary_index is not None
                else None
            )
            if type(text) is not str or not text.strip():
                raise ContextError("context_unavailable")
            result.append(
                Message(
                    role="assistant",
                    content=f"[Generated summary; source entries {_ranges(part)}]\n{text}",
                )
            )
        else:
            raise ContextError("context_invalid")
    return tuple(result)
