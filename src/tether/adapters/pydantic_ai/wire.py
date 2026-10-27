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


class StreamingWireValidator:
    """Validate bounded OpenAI-compatible SSE frames before the SDK sees them."""

    def __init__(self, tools: tuple[ToolSpec, ...], limits: ModelExecutionLimits) -> None:
        self.tools, self.limits = tools, limits
        self.pending = bytearray()
        self.line = bytearray()
        self.data_lines: list[bytes] = []
        self.bytes_seen = 0
        self.content: list[str] = []
        self.calls: dict[int, dict[str, object]] = {}
        self.usage: JsonValue | None = None
        self.response_id: str | None = None
        self.model: str | None = None
        self.created: int | None = None
        self.finish_reason: str | None = None
        self.done = False
        self.result: ModelResponse | None = None

    def feed(self, chunk: bytes) -> None:
        if self.done and chunk.strip():
            raise ModelUnavailable("invalid_response")
        self.bytes_seen += len(chunk)
        if self.bytes_seen > self.limits.max_response_bytes:
            raise ModelUnavailable("response_too_large")
        for byte in chunk:
            if byte == 10:
                line = bytes(self.line).removesuffix(b"\r")
                self.line.clear()
                if not line:
                    self._event()
                elif line.startswith(b"data:"):
                    value = line[5:]
                    self.data_lines.append(value[1:] if value.startswith(b" ") else value)
                elif line.startswith(b":"):
                    continue
                else:
                    raise ModelUnavailable("invalid_response")
            else:
                self.line.append(byte)
                if len(self.line) > self.limits.max_response_bytes:
                    raise ModelUnavailable("response_too_large")

    def _event(self) -> None:
        if not self.data_lines:
            return
        if self.done:
            raise ModelUnavailable("invalid_response")
        data = b"\n".join(self.data_lines)
        self.data_lines.clear()
        if data == b"[DONE]":
            if self.finish_reason is None:
                raise ModelUnavailable("invalid_response")
            self.result = self._finish()
            self.done = True
            return
        try:
            value: JsonValue = json.loads(data, object_pairs_hook=json_object)
            canonical(value)
            self._chunk(value)
        except ModelUnavailable:
            raise
        except (ValueError, TypeError, UnicodeError, AttributeError):
            raise ModelUnavailable("invalid_response") from None

    def _chunk(self, value: JsonValue) -> None:
        if not isinstance(value, dict) or value.get("object") != "chat.completion.chunk":
            raise ModelUnavailable("invalid_response")
        if set(value) - {
            "id",
            "object",
            "created",
            "model",
            "choices",
            "usage",
            "system_fingerprint",
            "service_tier",
        }:
            raise ModelUnavailable("unsupported_content")
        response_id, model, created = value.get("id"), value.get("model"), value.get("created")
        if (
            not isinstance(response_id, str)
            or not isinstance(model, str)
            or isinstance(created, bool)
            or not isinstance(created, int)
        ):
            raise ModelUnavailable("invalid_response")
        if self.response_id is None:
            self.response_id, self.model, self.created = response_id, model, created
        elif (response_id, model, created) != (self.response_id, self.model, self.created):
            raise ModelUnavailable("invalid_response")
        choices = value.get("choices")
        if not isinstance(choices, list) or len(choices) > 1:
            raise ModelUnavailable("invalid_response")
        if "usage" in value and value["usage"] is not None:
            self.usage = value["usage"]
            usage_from_wire(self.usage)
        if not choices:
            return
        if self.finish_reason is not None:
            raise ModelUnavailable("invalid_response")
        self._choice(choices[0])

    def _choice(self, choice: JsonValue) -> None:
        if not isinstance(choice, dict) or set(choice) - {"index", "delta", "finish_reason"}:
            raise ModelUnavailable("unsupported_content")
        if choice.get("index") != 0:
            raise ModelUnavailable("invalid_response")
        delta = choice.get("delta")
        if not isinstance(delta, dict) or set(delta) - {"role", "content", "tool_calls"}:
            raise ModelUnavailable("unsupported_content")
        if "role" in delta and delta["role"] != "assistant":
            raise ModelUnavailable("invalid_response")
        self._content_and_tools(delta)
        finish = choice.get("finish_reason")
        if finish is not None:
            if not isinstance(finish, str):
                raise ModelUnavailable("invalid_response")
            self.finish_reason = finish

    def _content_and_tools(self, delta: dict[str, JsonValue]) -> None:
        content = delta.get("content")
        if content is not None:
            if not isinstance(content, str):
                raise ModelUnavailable("unsupported_content")
            self.content.append(content)
        tool_calls = delta.get("tool_calls")
        if tool_calls is not None:
            if not isinstance(tool_calls, list):
                raise ModelUnavailable("invalid_response")
            for call in tool_calls:
                self._tool_delta(call)

    def _tool_delta(self, value: JsonValue) -> None:
        if not isinstance(value, dict) or set(value) - {"index", "id", "type", "function"}:
            raise ModelUnavailable("unsupported_content")
        index = value.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ModelUnavailable("invalid_response")
        call = self.calls.setdefault(index, {"id": None, "name": None, "arguments": ""})
        if "id" in value:
            if call["id"] is not None or not isinstance(value["id"], str):
                raise ModelUnavailable("invalid_response")
            call["id"] = value["id"]
        if "type" in value and value["type"] != "function":
            raise ModelUnavailable("unsupported_content")
        if "function" in value:
            self._function_delta(call, value["function"])

    def _function_delta(self, call: dict[str, object], value: JsonValue) -> None:
        if not isinstance(value, dict) or set(value) - {"name", "arguments"}:
            raise ModelUnavailable("unsupported_content")
        name, arguments = value.get("name"), value.get("arguments")
        if name is not None:
            if call["name"] is not None or not isinstance(name, str):
                raise ModelUnavailable("invalid_response")
            call["name"] = name
        if arguments is not None:
            if not isinstance(arguments, str):
                raise ModelUnavailable("invalid_response")
            combined = str(call["arguments"]) + arguments
            if len(combined.encode("utf-8")) > self.limits.max_tool_argument_bytes:
                raise ModelUnavailable("response_too_large")
            call["arguments"] = combined

    def _finish(self) -> ModelResponse:
        if self.response_id is None or self.finish_reason is None:
            raise ModelUnavailable("invalid_response")
        tool_calls: list[dict[str, JsonValue]] = []
        for call in self.calls.values():
            if not isinstance(call["id"], str) or not isinstance(call["name"], str):
                raise ModelUnavailable("invalid_response")
            call_id, name, arguments = str(call["id"]), str(call["name"]), str(call["arguments"])
            function: dict[str, JsonValue] = {"name": name, "arguments": arguments}
            tool_calls.append({"id": call_id, "type": "function", "function": function})
        tool_call_values: list[JsonValue] = []
        tool_call_values.extend(tool_calls)
        message: dict[str, JsonValue] = {
            "role": "assistant",
            "content": "".join(self.content) or None,
            "tool_calls": tool_call_values or None,
        }
        choice: dict[str, JsonValue] = {
            "index": 0,
            "finish_reason": self.finish_reason,
            "message": message,
        }
        body: dict[str, JsonValue] = {
            "id": self.response_id,
            "choices": [choice],
            "usage": self.usage,
        }
        return validate_wire(canonical(body), self.tools, self.limits)

    def finish(self) -> ModelResponse:
        if self.line or self.data_lines:
            raise ModelUnavailable("invalid_response")
        if not self.done or self.result is None:
            raise ModelUnavailable("invalid_response")
        return self.result
