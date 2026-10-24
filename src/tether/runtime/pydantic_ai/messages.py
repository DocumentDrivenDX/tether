"""One text/function-tool codec. Unsupported media/reasoning is explicit."""

from __future__ import annotations

import json
from typing import cast

from pydantic import JsonValue, ValidationError
from pydantic_ai.messages import (
    FinishReason,
    ModelMessage,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.messages import (
    ModelRequest as SDKRequest,
)
from pydantic_ai.messages import (
    ModelResponse as SDKResponse,
)
from pydantic_ai.usage import RequestUsage

from tether.models.canonical import canonical
from tether.types import Message, ModelResponse, ModelUnavailable, TokenUsage, ToolCall

from .attachments import model_view

CODEC = "tether-text-tools-v1"


def json_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def arguments(value: object) -> dict[str, JsonValue]:
    try:
        parsed = (
            json.loads(value, object_pairs_hook=json_object) if isinstance(value, str) else value
        )
        parsed = ToolCall.model_validate(dict(id="codec", name="codec", arguments=parsed)).arguments
        canonical(parsed)
        return parsed
    except (ValueError, TypeError):
        raise ModelUnavailable("invalid_response") from None


def to_sdk_response(response: ModelResponse, model: str) -> SDKResponse:
    parts: list[TextPart | ToolCallPart] = []
    if response.content:
        parts.append(TextPart(response.content))
    parts.extend(ToolCallPart(call.name, call.arguments, call.id) for call in response.tool_calls)
    usage = response.usage
    sdk_usage = (
        RequestUsage(unmeasured_requests=1)
        if usage is None
        else RequestUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            reasoning_tokens=usage.reasoning_tokens,
        )
    )
    reasons = {
        "stop": "stop",
        "tool_calls": "tool_call",
        "length": "length",
        "filtered": "content_filter",
        "unknown": "error",
    }
    return SDKResponse(
        parts=parts,
        model_name=model,
        usage=sdk_usage,
        finish_reason=cast("FinishReason", reasons[response.finish_reason]),
        provider_response_id=response.receipt.response_id
        if response.receipt
        else response.request_id,
        metadata={CODEC: response.model_dump(mode="json")},
    )


def _normalize_response(response: SDKResponse, usage: TokenUsage | None = None) -> ModelResponse:
    content: list[str] = []
    calls: list[ToolCall] = []
    for part in response.parts:
        if not isinstance(part, (TextPart, ToolCallPart)):
            raise ModelUnavailable("unsupported_content")
        if isinstance(part, ToolCallPart) and part.tool_kind not in (None, "function"):
            raise ModelUnavailable("unsupported_content")
        if part.provider_details:
            raise ModelUnavailable("unsupported_content")
        if isinstance(part, TextPart):
            content.append(part.content)
        else:
            calls.append(
                ToolCall(id=part.tool_call_id, name=part.tool_name, arguments=arguments(part.args))
            )
    reasons = {
        "stop": "stop",
        "tool_call": "tool_calls",
        "length": "length",
        "content_filter": "filtered",
    }
    value = dict(
        content="".join(content),
        tool_calls=tuple(calls),
        finish_reason=reasons.get(response.finish_reason or "", "unknown"),
        request_id=response.provider_response_id,
    )
    try:
        normalized = ModelResponse.model_validate(value)
        if response.metadata and CODEC in response.metadata:
            original = ModelResponse.model_validate(response.metadata[CODEC])
            if (
                original.content != normalized.content
                or original.tool_calls != normalized.tool_calls
                or original.finish_reason != normalized.finish_reason
            ):
                raise ModelUnavailable("sdk_projection_changed")
            return original
        return normalized.model_copy(update={"usage": usage})
    except (ValidationError, ValueError):
        raise ModelUnavailable("invalid_response") from None


def from_sdk_response(response: SDKResponse, usage: TokenUsage | None = None) -> ModelResponse:
    try:
        return _normalize_response(response, usage)
    except (ValueError, ValidationError):
        raise ModelUnavailable("invalid_response") from None


def to_sdk_messages(
    messages: tuple[Message, ...], system: str = "", *, require_closed: bool = False
) -> list[ModelMessage]:
    result: list[ModelMessage] = [SDKRequest(parts=[SystemPromptPart(system)])] if system else []
    names: dict[str, str] = {}
    for original in messages:
        message = model_view(original)
        if message.role == "assistant":
            for call in message.tool_calls:
                if call.id in names:
                    raise ModelUnavailable("ambiguous_call_id")
                names[call.id] = call.name
            result.append(
                to_sdk_response(
                    ModelResponse(
                        content=message.content,
                        tool_calls=message.tool_calls,
                        finish_reason="tool_calls" if message.tool_calls else "stop",
                    ),
                    "history",
                )
            )
        elif message.role == "user" and not message.tool_calls and message.tool_call_id is None:
            result.append(SDKRequest(parts=[UserPromptPart(message.content)]))
        elif message.role == "tool" and message.tool_call_id in names and not message.tool_calls:
            call_id = message.tool_call_id
            result.append(
                SDKRequest(
                    parts=[
                        ToolReturnPart(
                            names.pop(call_id),
                            message.content,
                            call_id,
                            outcome="failed" if message.is_error else "success",
                        )
                    ]
                )
            )
        else:
            raise ModelUnavailable("invalid_history")
    if require_closed and names:
        raise ModelUnavailable("invalid_history")
    return result


def from_sdk_messages(messages: list[ModelMessage]) -> tuple[str, tuple[Message, ...]]:
    system: list[str] = []
    names: dict[str, str] = {}
    result: list[Message] = []
    for message in messages:
        if isinstance(message, SDKResponse):
            result.append(source_response(message, names))
        else:
            if message.instructions or message.metadata:
                raise ModelUnavailable("unsupported_content")
            for part in message.parts:
                if isinstance(part, SystemPromptPart):
                    system.append(part.content)
                elif isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    result.append(Message(role="user", content=part.content))
                elif isinstance(part, ToolReturnPart) and isinstance(part.content, str):
                    result.append(source_tool_return(part, names))
                elif isinstance(part, RetryPromptPart):
                    raise ModelUnavailable("unsupported_correction_codec")
                else:
                    raise ModelUnavailable("unsupported_content")
    to_sdk_messages(tuple(result))
    return "\n\n".join(system), tuple(result)


def source_response(message: SDKResponse, names: dict[str, str]) -> Message:
    response = from_sdk_response(message)
    for call in response.tool_calls:
        if call.id in names:
            raise ModelUnavailable("ambiguous_call_id")
        names[call.id] = call.name
    return Message(role="assistant", content=response.content, tool_calls=response.tool_calls)


def source_tool_return(part: ToolReturnPart, names: dict[str, str]) -> Message:
    if names.pop(part.tool_call_id, None) != part.tool_name:
        raise ModelUnavailable("invalid_history")
    if not isinstance(part.content, str) or part.outcome not in {"success", "failed"}:
        raise ModelUnavailable("unsupported_content")
    return Message(
        role="tool",
        content=part.content,
        tool_call_id=part.tool_call_id,
        is_error=part.outcome == "failed",
    )
