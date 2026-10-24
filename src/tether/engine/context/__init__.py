"""Context configuration is explicit; FullContext is the default planner, never a model loop."""

from tether.context import FullContext
from tether.context.validation import copied
from tether.types.context import ContextConfig, ContextError, ContextStrategy, TokenEstimator


class ContextBoundary:
    def __init__(
        self,
        config: ContextConfig | None,
        estimator: TokenEstimator | None,
        strategy: ContextStrategy | None,
    ) -> None:
        self.config = copied(ContextConfig, config) if config is not None else None
        self.estimator = estimator
        self.strategy = strategy if strategy is not None else FullContext()

    def configuration(self, tier: str) -> ContextConfig:
        if self.config is None or self.estimator is None or tier not in self.config.limits:
            raise ContextError("context_unconfigured")
        return copied(ContextConfig, self.config)

    def output_tokens(self, tier: str) -> int:
        return (
            self.config.limits[tier].output_tokens
            if self.config and tier in self.config.limits
            else 1
        )
