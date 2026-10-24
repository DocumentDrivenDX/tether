"""Deterministic built-ins select complete sources; final wire fit remains engine-owned."""

from tether.types.context import ContextError, ContextInput, ContextPart, ContextPlan, SourceRange

from .validation import validated_input, validated_plan


def source_part(indices: tuple[int, ...], *, omit: bool = False) -> ContextPart:
    return ContextPart(
        kind="omit" if omit else "source",
        ranges=(SourceRange(start=indices[0], end=indices[-1]),),
        reason="budget" if omit else None,
    )


class FullContext:
    id = "full"
    version = "1"

    async def plan(self, context: ContextInput) -> ContextPlan:
        context = validated_input(context)
        if (
            context.fixed_estimate.tokens + sum(g.estimate.tokens for g in context.groups)
            > context.limits.input_tokens
        ):
            raise ContextError("context_exhausted")
        return validated_plan(
            context,
            ContextPlan(
                strategy_id=self.id,
                strategy_version=self.version,
                parts=tuple(source_part(g.indices) for g in context.groups),
            ),
        )


class TailContext:
    id = "tail"
    version = "1"

    async def plan(self, context: ContextInput) -> ContextPlan:
        context = validated_input(context)
        users = {s.index for s in context.sources if s.message.role == "user"}
        required = context.protected | users
        selected = {
            n for n, group in enumerate(context.groups) if required.intersection(group.indices)
        }
        used = context.fixed_estimate.tokens + sum(
            context.groups[n].estimate.tokens for n in selected
        )
        if used > context.limits.input_tokens:
            raise ContextError("context_exhausted")
        for number in reversed(range(len(context.groups))):
            if (
                number not in selected
                and used + context.groups[number].estimate.tokens <= context.limits.input_tokens
            ):
                selected.add(number)
                used += context.groups[number].estimate.tokens
        return validated_plan(
            context,
            ContextPlan(
                strategy_id=self.id,
                strategy_version=self.version,
                parts=tuple(
                    source_part(g.indices, omit=n not in selected)
                    for n, g in enumerate(context.groups)
                ),
            ),
        )
