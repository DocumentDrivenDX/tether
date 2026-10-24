"""Opt-in deterministic summarization proposals; only Engine may execute them."""

from dataclasses import dataclass

from tether.types.context import ContextError, ContextInput, ContextPart, ContextPlan, SourceRange

from .strategies import source_part
from .validation import validated_input, validated_plan


@dataclass(frozen=True)
class SummarizingContext:
    summarize_user_messages: bool = False
    id = "summarizing"
    version = "1"

    def __post_init__(self) -> None:
        if type(self.summarize_user_messages) is not bool:
            raise ValueError("context_summary_options")

    def _blocks(self, context: ContextInput) -> tuple[tuple[int, ...], ...]:
        protected = set(context.protected)
        if not self.summarize_user_messages:
            protected.update(s.index for s in context.sources if s.message.role == "user")
        blocks: list[tuple[int, ...]] = []
        current: list[int] = []
        for number, group in enumerate(context.groups):
            split = bool(current) and any(
                context.groups[current[-1]].indices[-1] < f < group.indices[0]
                for f in context.filtered_indices
            )
            if (protected.intersection(group.indices) or split) and current:
                blocks.append(tuple(current))
                current = []
            if not protected.intersection(group.indices):
                current.append(number)
        if current:
            blocks.append(tuple(current))
        return tuple(blocks)

    async def plan(self, context: ContextInput) -> ContextPlan:
        context = validated_input(context)
        parts = tuple(source_part(g.indices) for g in context.groups)
        total = context.fixed_estimate.tokens + sum(g.estimate.tokens for g in context.groups)
        if total > context.limits.input_tokens:
            blocks = self._blocks(context)
            if not blocks:
                raise ContextError("context_exhausted")
            chosen = max(
                blocks, key=lambda block: sum(context.groups[n].estimate.tokens for n in block)
            )
            span = (
                SourceRange(
                    start=context.groups[chosen[0]].indices[0],
                    end=context.groups[chosen[-1]].indices[-1],
                ),
            )
            prior = next(
                (s for s in reversed(context.summaries) if s.record.source_ranges == span), None
            )
            compact = ContextPart(
                kind="summary" if prior is not None else "summarize",
                ranges=span,
                summary_index=prior.index if prior is not None else None,
            )
            parts = tuple(
                compact if n == chosen[0] else part
                for n, part in enumerate(parts)
                if n not in chosen or n == chosen[0]
            )
        return validated_plan(
            context, ContextPlan(strategy_id=self.id, strategy_version=self.version, parts=parts)
        )
