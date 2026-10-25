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
from openai import APIConnectionError
from pydantic_ai.exceptions import UnexpectedModelBehavior

from tether.models.canonical import copy_request
from tether.runtime.pydantic_ai.messages import from_sdk_response
from tether.types import (
    Credential,
    ModelExecutionLimits,
    ModelRequest,
    ModelResponse,
    ModelUnavailable,
    StreamItem,
    TextDelta,
)

from .estimator import OpenAIWireEstimator
from .native import request_native, validate_payload
from .tokenizer import TokenizerProfile
from .transport import AttemptTransport


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
        self._transport = (
            transport
            if transport is not None
            else httpx.AsyncHTTPTransport(retries=0, trust_env=False)
        )

    async def aclose(self) -> None:
        await self._transport.aclose()

    def _validate_request(self, request: ModelRequest) -> None:
        validate_payload(request, self._tiers)
        if request.attempts is None:
            raise ModelUnavailable("missing_attempt_recorder")
        if request.identity_mode != "caller" and request.credential is not None:
            raise ModelUnavailable("identity_mode_conflict")
        if request.identity_mode == "application" and self._application_credential is None:
            raise ModelUnavailable("missing_application_credential")

    def estimator(self, *, profile: TokenizerProfile, vocabulary: bytes) -> OpenAIWireEstimator:
        return OpenAIWireEstimator(
            tiers=self._tiers,
            output_limit_field=self._output_limit_field,
            profile=profile,
            vocabulary=vocabulary,
        )

    async def _once(self, request: ModelRequest, boundary: AttemptTransport) -> ModelResponse:
        error: Exception | None = None
        normalized: ModelResponse | None = None
        try:
            response = await request_native(
                request,
                base_url=self._base_url,
                model_name=self._tiers[request.model],
                output_limit_field=self._output_limit_field,
                transport=boundary,
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
        if (
            isinstance(error, UnexpectedModelBehavior)
            and boundary.response is not None
            and boundary.response.finish_reason == "unknown"
        ):
            # A validated observed unknown is incomplete evidence, never SDK execution authority.
            return boundary.response
        if error is not None:
            reason = (
                "transport_error" if isinstance(error, APIConnectionError) else "invalid_response"
            )
            raise ModelUnavailable(reason, receipt=boundary.receipt) from None
        observed = boundary.response
        if observed is None or normalized is None:
            raise ModelUnavailable("invalid_response")
        # A missing wire reason remains unknown even if the SDK defaults it to stop.
        finish_matches = observed.finish_reason in {"unknown", normalized.finish_reason}
        if (observed.content, observed.tool_calls) != (
            normalized.content,
            normalized.tool_calls,
        ) or not finish_matches:
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
