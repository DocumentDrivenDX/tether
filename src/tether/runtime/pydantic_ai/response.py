"""One bounded ModelPort response, shared by Agent and governed auxiliary requests."""

import asyncio

from tether.models import checked_stream, managed
from tether.ports import ModelPort
from tether.types import (
    ModelExecutionLimits,
    ModelRequest,
    ModelResponse,
    ModelUnavailable,
    TextDelta,
)

from .validation import validate_response


class IncompleteModelResponse(Exception):
    """A known accepted non-executable terminal; never an SDK or provider retry signal."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


async def bounded_response(
    port: ModelPort, request: ModelRequest, limits: ModelExecutionLimits, *, executable: bool = True
) -> ModelResponse:
    size = 0
    response: ModelResponse | None = None
    seconds = min(
        request.timeout_seconds or limits.request_timeout_seconds, limits.request_timeout_seconds
    )
    try:
        async with asyncio.timeout(seconds):
            async with managed(
                checked_stream(port, request, max_text_bytes=limits.max_response_bytes)
            ) as stream:
                async for item in stream:
                    if isinstance(item, TextDelta):
                        size += len(item.text.encode("utf-8"))
                        if size > limits.max_response_bytes:
                            raise ModelUnavailable("response_too_large")
                        recorder = request.attempts
                        disclose = getattr(recorder, "disclose_delta", None)
                        if disclose is not None:
                            await disclose(item.identity, item.text)
                    else:
                        response = item
    except TimeoutError:
        raise ModelUnavailable("timeout") from None
    if response is None:
        raise ModelUnavailable("incomplete_stream")
    validate_response(response, request.tools, limits, executable=executable)
    return ModelResponse.model_validate(response.model_dump(mode="json"))
