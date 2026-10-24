"""Explicit nonstreaming compatible profile using the native Pydantic AI model."""

from __future__ import annotations

import asyncio
import math
import random
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx2 as httpx
from openai import APIConnectionError, AsyncOpenAI
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.tools import ToolDefinition

from tether.models.canonical import copy_request
from tether.runtime.pydantic_ai.messages import from_sdk_response, to_sdk_messages
from tether.types import (
    Credential,
    ModelExecutionLimits,
    ModelRequest,
    ModelResponse,
    ModelUnavailable,
    StreamItem,
    TextDelta,
)

from .transport import AttemptTransport

RESERVED = frozenset(
    {
        "model",
        "messages",
        "tools",
        "tool_choice",
        "stream",
        "stream_options",
        "max_tokens",
        "max_completion_tokens",
        "max_output_tokens",
        "extra_headers",
        "extra_body",
        "timeout",
        "response_format",
        "n",
        "reasoning_effort",
        "parallel_tool_calls",
    }
)


def retry_delay(header: str | None, cap: float, attempt: int) -> float:
    try:
        value = float(header) if header is not None else math.nan
    except ValueError:
        try:
            date = parsedate_to_datetime(header or "")
            value = (date.astimezone(UTC) - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            value = math.nan
    if math.isfinite(value) and value >= 0:
        return min(value, cap)
    return random.SystemRandom().uniform(0, min(cap, 0.25 * 2 ** min(attempt, 10)))


class OpenAIAdapter:
    """Text/function-tool subset only. SDK retries and redirects are disabled."""

    provider = "openai-compatible"

    def __init__(
        self,
        *,
        base_url: str,
        tiers: dict[str, str],
        limits: ModelExecutionLimits,
        output_limit_field: str,
        application_credential: Credential | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("invalid_base_url")
        if output_limit_field not in {"max_tokens", "max_completion_tokens"}:
            raise ValueError("unsupported_output_limit")
        if not tiers or any(not k or not v for k, v in tiers.items()):
            raise ValueError("invalid_tier_map")
        if application_credential is not None and not callable(
            getattr(application_credential, "authorization", None)
        ):
            raise ValueError("invalid_application_credential")
        self._base_url = base_url
        self._tiers = dict(tiers)
        self._limits = limits.model_copy(deep=True)
        self._output_limit_field = output_limit_field
        self._application_credential = application_credential
        self._transport = transport or httpx.AsyncHTTPTransport(retries=0, trust_env=False)

    async def aclose(self) -> None:
        await self._transport.aclose()

    def _validate_request(self, request: ModelRequest) -> None:
        if request.model not in self._tiers:
            raise ModelUnavailable("unconfigured tier")
        if request.attempts is None:
            raise ModelUnavailable("missing_attempt_recorder")
        if request.identity_mode != "caller" and request.credential is not None:
            raise ModelUnavailable("identity_mode_conflict")
        if request.identity_mode == "application" and self._application_credential is None:
            raise ModelUnavailable("missing_application_credential")
        if RESERVED.intersection(request.extra):
            raise ModelUnavailable("parameter_conflict")
        if request.effort is not None:
            raise ModelUnavailable("unsupported_effort")

    async def _once(self, request: ModelRequest, boundary: AttemptTransport) -> ModelResponse:
        error: Exception | None = None
        normalized: ModelResponse | None = None
        async with (
            httpx.AsyncClient(
                transport=boundary,
                trust_env=False,
                follow_redirects=False,
                headers={"Accept-Encoding": "identity"},
            ) as http,
            AsyncOpenAI(
                api_key="-".join(("tether", "transport", "placeholder")),
                organization="",
                project="",
                admin_api_key="",
                webhook_secret="",
                base_url=self._base_url,
                max_retries=0,
                http_client=http,
            ) as client,
        ):
            model = OpenAIChatModel(
                self._tiers[request.model],
                provider=OpenAIProvider(openai_client=client),
                profile=OpenAIModelProfile(
                    openai_chat_supports_max_completion_tokens=self._output_limit_field
                    == "max_completion_tokens",
                    openai_supports_strict_tool_definition=False,
                ),
            )
            parameters = ModelRequestParameters(
                function_tools=[
                    ToolDefinition(
                        name=t.name,
                        description=t.description,
                        parameters_json_schema=t.input_schema,
                        strict=False,
                    )
                    for t in request.tools
                ]
            )
            settings = OpenAIChatModelSettings(
                max_tokens=request.max_output_tokens,
                extra_body=request.extra,
                parallel_tool_calls=False,
            )
            try:
                response = await model.request(
                    to_sdk_messages(request.messages, request.system), settings, parameters
                )
                normalized = from_sdk_response(
                    response, boundary.response.usage if boundary.response else None
                )
            except Exception as failure:
                error = failure
        if boundary.local_error is not None:
            raise boundary.local_error
        if isinstance(error, ModelUnavailable):
            raise error
        if error is not None:
            reason = (
                "transport_error" if isinstance(error, APIConnectionError) else "invalid_response"
            )
            raise ModelUnavailable(reason, receipt=boundary.receipt) from None
        observed = boundary.response
        if observed is None or normalized is None:
            raise ModelUnavailable("invalid_response")
        if (observed.content, observed.tool_calls, observed.finish_reason) != (
            normalized.content,
            normalized.tool_calls,
            normalized.finish_reason,
        ):
            raise ModelUnavailable("sdk_projection_changed")
        return observed

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamItem]:
        request = copy_request(request)
        self._validate_request(request)
        seconds = min(
            request.timeout_seconds or self._limits.request_timeout_seconds,
            self._limits.request_timeout_seconds,
        )
        try:
            async with asyncio.timeout(seconds):
                response = await self._retry(request)
                yield TextDelta(text=response.content)
                yield response
        except TimeoutError:
            raise ModelUnavailable("timeout") from None

    async def _retry(self, request: ModelRequest) -> ModelResponse:
        for attempt in range(self._limits.max_attempts):
            boundary = AttemptTransport(
                self._transport, request, self._limits, self._application_credential
            )
            try:
                return await self._once(request, boundary)
            except ModelUnavailable as error:
                if (
                    error.reason
                    not in {
                        "transport_error",
                        "http 429",
                        "http 500",
                        "http 502",
                        "http 503",
                        "http 504",
                    }
                    or attempt + 1 == self._limits.max_attempts
                ):
                    raise
                await asyncio.sleep(
                    retry_delay(boundary.retry_after, self._limits.max_retry_delay_seconds, attempt)
                )
        raise ModelUnavailable("attempts_exhausted")
