"""Fail-closed semantic limits shared by Agent and native model integrations."""

from tether.models.canonical import canonical
from tether.types import ModelExecutionLimits, ModelResponse, ModelUnavailable, ToolSpec


def validate_response(
    response: ModelResponse,
    tools: tuple[ToolSpec, ...],
    limits: ModelExecutionLimits,
    *,
    executable: bool = True,
) -> None:
    try:
        encoded = canonical(response.model_dump(mode="json"))
        argument_sizes = [len(canonical(call.arguments)) for call in response.tool_calls]
    except (ValueError, TypeError):
        raise ModelUnavailable("invalid_response") from None
    if len(encoded) > limits.max_response_bytes or any(
        size > limits.max_tool_argument_bytes for size in argument_sizes
    ):
        raise ModelUnavailable("response_too_large")
    ids = [call.id for call in response.tool_calls]
    names = {tool.name for tool in tools}
    if len(ids) != len(set(ids)) or any(call.name not in names for call in response.tool_calls):
        raise ModelUnavailable("invalid_response")
    inconsistent = (response.finish_reason == "stop" and bool(response.tool_calls)) or (
        response.finish_reason == "tool_calls" and not response.tool_calls
    )
    if inconsistent or (
        executable
        and not (
            (response.finish_reason == "stop" and not response.tool_calls)
            or (response.finish_reason == "tool_calls" and bool(response.tool_calls))
        )
    ):
        raise ModelUnavailable("invalid_terminal")
