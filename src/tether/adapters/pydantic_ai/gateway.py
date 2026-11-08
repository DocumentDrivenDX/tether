"""Explicitly selected Databricks AI Gateway SSE dialect (ADR-013, CONTRACT-001).

Every gateway-specific field is checked against a closed allow-list and then removed, so the
strict chunk validator and the SDK only ever see the `openai-chat-text-tools-v1` chunk shape.
Unknown fields, unknown filter structure and unknown content parts still refuse.
"""

from __future__ import annotations

from pydantic import JsonValue

from tether.types import ModelUnavailable

CHUNK_KEYS = frozenset(
    {"id", "object", "created", "model", "choices", "usage", "system_fingerprint", "service_tier"}
)
GATEWAY_CHUNK_KEYS = frozenset({"obfuscation", "latency_checkpoint", "routing"})
PREAMBLE_KEYS = frozenset({"id", "object", "created", "model", "choices", "prompt_filter_results"})
CHOICE_KEYS = frozenset({"index", "delta", "finish_reason"})
GATEWAY_CHOICE_KEYS = frozenset({"logprobs", "content_filter_results", "content_filter_result"})
DELTA_KEYS = frozenset({"role", "content", "tool_calls"})
FILTER_ENTRY_KEYS = frozenset({"filtered", "severity", "detected"})
SUMMARY_KEYS = frozenset({"type", "text", "signature"})


def _unsupported() -> ModelUnavailable:
    return ModelUnavailable("unsupported_content")


def _count(value: JsonValue) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def filter_results(value: JsonValue) -> bool:
    """Validate one category map; return whether any category reports filtering."""
    if not isinstance(value, dict):
        raise _unsupported()
    filtered = False
    for entry in value.values():
        if not isinstance(entry, dict) or set(entry) - FILTER_ENTRY_KEYS:
            raise _unsupported()
        flags = [entry[flag] for flag in ("filtered", "detected") if flag in entry]
        if not all(isinstance(flag, bool) for flag in flags):
            raise _unsupported()
        if "severity" in entry and not isinstance(entry["severity"], str):
            raise _unsupported()
        filtered = filtered or entry.get("filtered") is True
    return filtered


def filter_error(value: JsonValue) -> None:
    """A gateway report that its filter did not evaluate a chunk; shape-checked and dropped."""
    error = value.get("error") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or set(value) != {"error"}
        or not isinstance(error, dict)
        or set(error) != {"code", "message"}
        or not all(isinstance(item, str) for item in error.values())
    ):
        raise _unsupported()


def reasoning_parts(value: list[JsonValue]) -> None:
    """Reasoning-summary parts are validated and dropped; they never become answer text."""
    for part in value:
        if not isinstance(part, dict) or set(part) != {"type", "summary"}:
            raise _unsupported()
        summary = part["summary"]
        if part["type"] != "reasoning" or not isinstance(summary, list):
            raise _unsupported()
        for item in summary:
            if (
                not isinstance(item, dict)
                or not {"type", "text"} <= set(item) <= SUMMARY_KEYS
                or item["type"] != "summary_text"
                or not isinstance(item["text"], str)
                or not isinstance(item.get("signature", ""), str)
            ):
                raise _unsupported()


def gateway_metadata(value: dict[str, JsonValue]) -> None:
    if "obfuscation" in value and not isinstance(value["obfuscation"], str):
        raise _unsupported()
    latency = value.get("latency_checkpoint")
    if latency is not None and (
        not isinstance(latency, dict) or not all(_count(item) for item in latency.values())
    ):
        raise _unsupported()
    routing = value.get("routing")
    if routing is not None and (
        not isinstance(routing, dict) or not all(isinstance(item, str) for item in routing.values())
    ):
        raise _unsupported()


class GatewayStreamDialect:
    """Normalize one stream's gateway chunks to the strict chunk shape, or refuse."""

    def __init__(self) -> None:
        self.seen = False
        self.output_filtered = False

    def chunk(self, value: JsonValue) -> dict[str, JsonValue] | None:
        if not isinstance(value, dict):
            raise ModelUnavailable("invalid_response")
        if value.get("object") == "":
            self._preamble(value)
            return None
        if set(value) - CHUNK_KEYS - GATEWAY_CHUNK_KEYS:
            raise _unsupported()
        gateway_metadata(value)
        self.seen = True
        normalized = {key: item for key, item in value.items() if key in CHUNK_KEYS}
        choices = value.get("choices")
        if isinstance(choices, list):
            normalized["choices"] = [self._choice(choice) for choice in choices]
        return normalized

    def finish(self, finish_reason: str | None) -> None:
        if self.output_filtered and finish_reason != "content_filter":
            raise _unsupported()

    def _preamble(self, value: dict[str, JsonValue]) -> None:
        if self.seen or value.get("choices") != []:
            raise ModelUnavailable("invalid_response")
        if set(value) - PREAMBLE_KEYS:
            raise _unsupported()
        if any(not isinstance(value[key], str) for key in ("id", "model") if key in value) or (
            "created" in value and not _count(value["created"])
        ):
            raise ModelUnavailable("invalid_response")
        results = value.get("prompt_filter_results")
        if not isinstance(results, list):
            raise _unsupported()
        for result in results:
            if (
                not isinstance(result, dict)
                or set(result) != {"prompt_index", "content_filter_results"}
                or not _count(result["prompt_index"])
            ):
                raise _unsupported()
            if filter_results(result["content_filter_results"]):
                raise ModelUnavailable("prompt_filtered")
        self.seen = True

    def _choice(self, choice: JsonValue) -> dict[str, JsonValue]:
        if not isinstance(choice, dict) or set(choice) - CHOICE_KEYS - GATEWAY_CHOICE_KEYS:
            raise _unsupported()
        if choice.get("logprobs") is not None:
            raise _unsupported()
        if "content_filter_results" in choice and filter_results(choice["content_filter_results"]):
            self.output_filtered = True
        if "content_filter_result" in choice:
            filter_error(choice["content_filter_result"])
        delta = choice.get("delta")
        if not isinstance(delta, dict) or set(delta) - DELTA_KEYS - {"refusal"}:
            raise _unsupported()
        if delta.get("refusal") is not None:
            raise _unsupported()
        normalized_delta = {key: item for key, item in delta.items() if key in DELTA_KEYS}
        content = delta.get("content")
        if isinstance(content, list):
            reasoning_parts(content)
            del normalized_delta["content"]
        normalized: dict[str, JsonValue] = {
            key: item for key, item in choice.items() if key in CHOICE_KEYS
        }
        normalized["delta"] = normalized_delta
        return normalized
