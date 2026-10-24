"""Bounded compatible-response validation before native SDK parsing."""

import json

from pydantic import JsonValue, TypeAdapter, ValidationError

from tether.models.canonical import canonical
from tether.runtime.pydantic_ai.messages import arguments, json_object
from tether.runtime.pydantic_ai.validation import validate_response
from tether.types import (
    ModelExecutionLimits,
    ModelResponse,
    ModelUnavailable,
    TokenUsage,
    ToolCall,
    ToolSpec,
)


def usage_from_wire(value: object) -> TokenUsage | None:
    if value is None:
        return None
    try:
        payload = TypeAdapter(dict[str, JsonValue]).validate_python(value)
        prompt = TypeAdapter(dict[str, JsonValue]).validate_python(
            {} if payload.get("prompt_tokens_details") is None else payload["prompt_tokens_details"]
        )
        completion = TypeAdapter(dict[str, JsonValue]).validate_python(
            {}
            if payload.get("completion_tokens_details") is None
            else payload["completion_tokens_details"]
        )
        return TokenUsage.model_validate(
            dict(
                input_tokens=payload["prompt_tokens"],
                output_tokens=payload["completion_tokens"],
                cache_read_tokens=prompt.get("cached_tokens"),
                cache_write_tokens=prompt.get("cache_write_tokens"),
                reasoning_tokens=completion.get("reasoning_tokens"),
            )
        )
    except (KeyError, TypeError, AttributeError, ValidationError):
        raise ModelUnavailable("invalid_response") from None


def validate_wire(
    data: bytes, tools: tuple[ToolSpec, ...], limits: ModelExecutionLimits
) -> ModelResponse:
    if len(data) > limits.max_response_bytes:
        raise ModelUnavailable("response_too_large")
    try:
        value = json.loads(data, object_pairs_hook=json_object)
        canonical(value)
        choices = value["choices"]
        if len(choices) != 1 or choices[0].get("index") != 0:
            raise ValueError("ambiguous_choice")
        choice = choices[0]
        message = choice["message"]
        if message.get("role") != "assistant":
            raise ValueError("invalid_role")
        if any(
            message.get(k)
            for k in ("reasoning_content", "reasoning", "audio", "refusal", "function_call")
        ):
            raise ModelUnavailable("unsupported_content")
        if set(message) - {"role", "content", "tool_calls", "refusal"}:
            raise ModelUnavailable("unsupported_content")
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise ModelUnavailable("unsupported_content")
        calls = tuple(wire_call(call, limits) for call in message.get("tool_calls") or ())
        reasons = {
            "stop": "stop",
            "tool_calls": "tool_calls",
            "length": "length",
            "content_filter": "filtered",
        }
        response = ModelResponse.model_validate(
            dict(
                content=content or "",
                tool_calls=calls,
                request_id=value.get("id"),
                finish_reason=reasons.get(choice.get("finish_reason"), "unknown"),
                usage=usage_from_wire(value.get("usage")),
            )
        )
        validate_response(response, tools, limits, executable=False)
        return response
    except ModelUnavailable:
        raise
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ModelUnavailable("invalid_response") from None


def wire_call(value: dict[str, JsonValue], limits: ModelExecutionLimits) -> ToolCall:
    function = value["function"]
    if value.get("type") != "function" or not isinstance(function, dict):
        raise ModelUnavailable("invalid_response")
    raw = function.get("arguments")
    if not isinstance(raw, str):
        raise ModelUnavailable("invalid_response")
    if len(raw.encode("utf-8")) > limits.max_tool_argument_bytes:
        raise ModelUnavailable("response_too_large")
    return ToolCall.model_validate(
        dict(id=value["id"], name=function["name"], arguments=arguments(raw))
    )


def observed_usage(data: bytes) -> TokenUsage | None:
    """Retain valid typed usage even when the bounded response output is invalid."""
    try:
        value: JsonValue = json.loads(data, object_pairs_hook=json_object)
        canonical(value)
        return usage_from_wire(value.get("usage")) if isinstance(value, dict) else None
    except (ModelUnavailable, ValueError, UnicodeError):
        return None
