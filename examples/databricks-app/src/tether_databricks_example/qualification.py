"""Deterministic model and explicit budgets for deployment qualification only."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from tether.client import (
    AgentSpec,
    ClientLimits,
    ClientSettings,
    ContextConfig,
    Decision,
    ModelExecutionLimits,
    ModelLimits,
    ModelRequest,
    ModelResponse,
    ProvidedStorage,
    ResponseReceipt,
    StreamItem,
    TextDelta,
    TokenEstimate,
)
from tether.client.testing import scripted_model


class QualificationModel:
    """Return fixed text; optional bounded hold is for restart qualification."""

    provider = "fabricated-qualification"

    def __init__(self, *, hold_seconds: float = 0) -> None:
        if not 0 <= hold_seconds <= 240:
            raise ValueError("qualification_hold_out_of_range")
        self._hold_seconds = hold_seconds

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamItem]:
        answer = "Fabricated Tether qualification response."
        receipt = ResponseReceipt(
            provider=self.provider,
            endpoint="offline-qualification",
            schema_profile="fabricated-response-v1",
            request_id_source="none",
        )
        model = scripted_model(
            [TextDelta(text=answer)]
            + (
                [ModelResponse(content=answer, receipt=receipt, finish_reason="stop")]
                if self._hold_seconds == 0
                else []
            )
        )
        async for item in model.stream(request):
            yield item
        if self._hold_seconds:
            await asyncio.sleep(self._hold_seconds)
            yield ModelResponse(content=answer, receipt=receipt, finish_reason="stop")


class QualificationPolicy:
    async def authorize(self, request: object) -> Decision:
        del request
        return Decision(allowed=True, reason="fabricated_example_only")


class QualificationEstimator:
    async def estimate(self, request: ModelRequest) -> TokenEstimate:
        size = len(request.system) + sum(len(message.content) for message in request.messages)
        return TokenEstimate(
            tokens=max(1, size // 4),
            method="fabricated-character-estimate",
            revision="1",
            quality="approximate",
        )


def qualification_client_settings(
    storage: ProvidedStorage, *, hold_seconds: float = 0
) -> ClientSettings:
    """A deterministic model profile for qualification, never an app default."""
    return ClientSettings(
        model=QualificationModel(hold_seconds=hold_seconds),
        storage=storage,
        policy=QualificationPolicy(),
        client_limits=ClientLimits(
            max_page_size=100,
            max_call_ids=8,
            max_read_bytes=1_048_576,
            max_lookup_entries=1000,
            read_timeout_seconds=15,
            heartbeat_interval_seconds=1,
            shutdown_timeout_seconds=5,
            follower_queue_size=32,
            max_followers=8,
            max_active_turns=8,
            max_request_bytes=65536,
        ),
        model_limits=ModelExecutionLimits(
            max_response_bytes=8192,
            max_tool_argument_bytes=4096,
            max_attempts=1,
            request_timeout_seconds=max(10, hold_seconds + 15),
            max_retry_delay_seconds=0.1,
        ),
        context_config=ContextConfig(
            limits={
                tier: ModelLimits(
                    profile_id="qualification",
                    revision="1",
                    context_window_tokens=4096,
                    output_tokens=512,
                    safety_margin_tokens=128,
                )
                for tier in ("fabricated-qualification",)
            },
            max_source_bytes=1_048_576,
            timeout_seconds=30,
        ),
        estimator=QualificationEstimator(),
        specs=(
            AgentSpec(
                id="qualification",
                version="1",
                title="Fabricated qualification model",
                instructions="Return the fixed qualification response.",
                model="fabricated-qualification",
            ),
        ),
        identity_mode="caller",
        resume="off",
        default_spec="qualification",
    )


__all__ = ["QualificationModel", "qualification_client_settings"]
