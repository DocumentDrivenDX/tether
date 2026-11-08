"""Explicit optional compatible-model construction without exposing adapter internals."""

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Literal

from tether.types import ModelExecutionLimits, ModelRequest, StreamItem
from tether.types.context import TokenEstimator
from tether.types.tokenizer import TokenizerProfile as TokenizerSettings


@dataclass(frozen=True)
class CompatibleModelSettings:
    base_url: str
    tiers: Mapping[str, str]
    limits: ModelExecutionLimits
    output_limit_field: Literal["max_tokens", "max_completion_tokens"]
    supports_parallel_tool_calls: bool = True
    request_tags_header: str = "X-Tether-Request-Tags"
    stream_profile: Literal["openai-chat-text-tools-v1", "databricks-ai-gateway-stream-v1"] = (
        "openai-chat-text-tools-v1"
    )


class CompatibleModel:
    """Own the explicitly selected adapter; callers own this resource's lifetime."""

    provider = "openai-compatible"

    def __init__(self, settings: CompatibleModelSettings) -> None:
        try:
            from tether.adapters.openai_compat import OpenAICompatModel
        except ImportError:
            raise RuntimeError("openai_extra_required") from None
        self._adapter = OpenAICompatModel(
            base_url=settings.base_url,
            tiers=dict(settings.tiers),
            limits=settings.limits,
            output_limit_field=settings.output_limit_field,
            supports_parallel_tool_calls=settings.supports_parallel_tool_calls,
            request_tags_header=settings.request_tags_header,
            stream_profile=settings.stream_profile,
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamItem]:
        async for item in self._adapter.stream(request):
            yield item

    def estimator(self, *, profile: TokenizerSettings, vocabulary: bytes) -> TokenEstimator:
        return self._adapter.estimator(profile=profile, vocabulary=vocabulary)

    async def aclose(self) -> None:
        await self._adapter.aclose()


__all__ = ["CompatibleModel", "CompatibleModelSettings", "TokenizerSettings"]
