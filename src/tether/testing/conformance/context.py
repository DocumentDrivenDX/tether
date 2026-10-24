"""Logical context port checks with explicit profile-oracle and executor obligations."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Literal

from tether.context.materialization import materialize
from tether.context.validation import validated_plan
from tether.types import AttachmentRef, Message, ModelRequest, RequestTags, ToolCall, ToolSpec
from tether.types.canonical import canonical, digest
from tether.types.context import (
    ContextError,
    ContextGroup,
    ContextInput,
    ContextSource,
    ContextStrategy,
    ModelLimits,
    TokenEstimate,
    TokenEstimator,
)

from .types import ConformanceFailure, SuiteReport, require


@dataclass(frozen=True)
class TokenEstimatorFixture:
    estimator: TokenEstimator
    bounds: Callable[[ModelRequest], tuple[int, int]]
    evidence: Literal["fabricated", "wire_oracle"] = "fabricated"
    attachments: Literal["estimates", "refuses"] = "estimates"


ContextStrategyFactory = Callable[[], AbstractAsyncContextManager[ContextStrategy]]
TokenEstimatorFactory = Callable[[], AbstractAsyncContextManager[TokenEstimatorFixture]]


def suite_timeout(seconds: float) -> float:
    if type(seconds) not in (int, float) or not 0 < seconds < float("inf"):
        raise ValueError("context_suite_timeout")
    return seconds


def input_case(grouped: bool) -> ContextInput:
    estimate = TokenEstimate(tokens=10, method="fixture", revision="1", quality="approximate")
    sources = (
        ContextSource(index=0, message=Message(role="user", content="Fabricated old input")),
        ContextSource(index=2, message=Message(role="assistant", content="Fabricated response")),
        ContextSource(index=4, message=Message(role="user", content="Fabricated current input")),
    )
    if grouped:
        sources = (
            ContextSource(
                index=0,
                message=Message(
                    role="assistant",
                    content="",
                    tool_calls=(
                        ToolCall(id="call", name="fixture", arguments={"value": "Fabricated"}),
                    ),
                ),
            ),
            ContextSource(
                index=2,
                message=Message(role="tool", tool_call_id="call", content="Fabricated result"),
            ),
            sources[2],
        )
    groups = ((0, 2), (4,)) if grouped else ((0,), (2,), (4,))
    return ContextInput(
        session_id="fabricated-session",
        through_index=4,
        sources=sources,
        summaries=(),
        protected=frozenset({4}),
        limits=ModelLimits(
            profile_id="fixture",
            revision="1",
            context_window_tokens=10000,
            output_tokens=128,
            safety_margin_tokens=64,
        ),
        source_view_digest=digest(["fabricated-source", grouped]),
        fixed_estimate=estimate,
        groups=tuple(ContextGroup(indices=group, estimate=estimate) for group in groups),
    )


async def _plan_case(strategy: ContextStrategy, grouped: bool) -> tuple[str, ...]:
    context = input_case(grouped)
    before = canonical(context.model_dump(mode="json"))
    plan = await strategy.plan(context)
    require(canonical(context.model_dump(mode="json")) == before, "context_input_immutable")
    try:
        plan = validated_plan(context, plan)
    except ContextError:
        raise ConformanceFailure("context_plan_valid") from None
    require(
        (plan.strategy_id, plan.strategy_version) == (strategy.id, strategy.version),
        "context_strategy_identity",
    )
    if any(p.kind == "summarize" for p in plan.parts):
        try:
            materialize(context, plan)
        except ContextError as error:
            require(error.code == "context_invalid", "context_uncommitted_summary")
        else:
            raise ConformanceFailure("context_uncommitted_summary")
        return ("context_uncommitted_summary",)
    first = materialize(context, plan)
    second = materialize(context, plan)
    require(first == second, "context_materialization_deterministic")
    return ("context_materialization_deterministic",)


async def context_strategy_suite(
    factory: ContextStrategyFactory,
    *,
    timeout_seconds: float = 10,
) -> SuiteReport:
    checks = {"context_plan_valid", "context_input_immutable", "context_strategy_identity"}
    async with asyncio.timeout(suite_timeout(timeout_seconds)), factory() as strategy:
        for grouped in (False, True):
            checks.update(await _plan_case(strategy, grouped))
    return SuiteReport(
        suite="context_strategy",
        capabilities={"contract": 1},
        checks=tuple(sorted(checks)),
        required_external=(
            "source_authorization_provenance",
            "strategy_byte_deadline_bounds",
            "protected_pending_unknown_groups",
            "full_request_fit",
            "summary_commit_recovery",
            "property_coverage_adversaries",
        ),
    )


def estimator_cases() -> tuple[tuple[str, ModelRequest], ...]:
    base = ModelRequest(
        model="fixture",
        tags=RequestTags(
            session_id="fabricated-session",
            turn_id="phase",
            model_call_id="call",
            principal="fabricated",
            spec_id="fixture",
            spec_version="1",
        ),
        system="",
        messages=(),
        identity_mode="anonymous",
        max_output_tokens=128,
    )
    return (
        ("framing", base),
        ("system", base.model_copy(update={"system": "Fabricated instructions. " * 64})),
        (
            "messages",
            base.model_copy(
                update={"messages": (Message(role="user", content="Fabricated input. " * 64),)}
            ),
        ),
        (
            "tools",
            base.model_copy(
                update={
                    "tools": (
                        ToolSpec(
                            name="fixture",
                            description="Fabricated tool",
                            input_schema={
                                "type": "object",
                                "properties": {
                                    "value": {
                                        "type": "string",
                                        "description": "Fabricated schema. " * 64,
                                    }
                                },
                            },
                        ),
                    )
                }
            ),
        ),
        (
            "attachments",
            base.model_copy(
                update={
                    "messages": (
                        Message(
                            role="user",
                            content="Fabricated",
                            attachments=(
                                AttachmentRef(
                                    id="fabricated",
                                    filename="fixture.png",
                                    media_type="image/png",
                                    size_bytes=32,
                                    read=True,
                                ),
                            ),
                        ),
                    )
                }
            ),
        ),
    )


async def _estimate_case(fixture: TokenEstimatorFixture, name: str, request: ModelRequest) -> None:
    before = canonical(request.model_dump(mode="json"))
    if name == "attachments" and fixture.attachments == "refuses":
        try:
            await fixture.estimator.estimate(request)
        except ContextError as error:
            require(error.code == "context_unmeasurable", "estimate_attachment_refusal")
        else:
            raise ConformanceFailure("estimate_attachment_refusal")
        require(canonical(request.model_dump(mode="json")) == before, "estimate_input_immutable")
        return
    try:
        first = TokenEstimate.model_validate(await fixture.estimator.estimate(request))
        second = TokenEstimate.model_validate(await fixture.estimator.estimate(request))
    except (ValueError, TypeError):
        raise ConformanceFailure("estimate_shape") from None
    require(first == second, "estimate_repeatable")
    require(canonical(request.model_dump(mode="json")) == before, "estimate_input_immutable")
    lower, upper = fixture.bounds(request)
    require(
        type(lower) is int and type(upper) is int and 0 <= lower <= upper, "estimate_oracle_bounds"
    )
    require(lower <= first.tokens <= upper, "estimate_" + name + "_bounds")


async def token_estimator_suite(
    factory: TokenEstimatorFactory,
    *,
    timeout_seconds: float = 10,
) -> SuiteReport:
    async with asyncio.timeout(suite_timeout(timeout_seconds)), factory() as fixture:
        for name, request in estimator_cases():
            await _estimate_case(fixture, name, request)
    return SuiteReport(
        suite="token_estimator",
        capabilities={
            "contract": 1,
            "evidence": fixture.evidence,
            "attachments": fixture.attachments,
        },
        checks=(
            "estimate_shape",
            "estimate_repeatable",
            "estimate_input_immutable",
            "estimate_oracle_bounds",
            "estimate_framing_bounds",
            "estimate_system_bounds",
            "estimate_messages_bounds",
            "estimate_tools_bounds",
            "estimate_attachment_refusal"
            if fixture.attachments == "refuses"
            else "estimate_attachments_bounds",
        ),
        required_external=(
            "wire_oracle_version_qualification",
            "real_serialized_payload_framing",
            "attachment_encoding_bound",
            "engine_pre_egress_fit_and_retries",
            "source_byte_deadline_bounds",
        ),
    )
