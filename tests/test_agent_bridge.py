"""Actual Agent execution through ModelPort, with all tool proposals deferred."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator

import pytest
from pydantic_ai import Conversation
from pydantic_ai.messages import ModelRequest as SDKRequest
from pydantic_ai.messages import ModelResponse as SDKResponse
from pydantic_ai.messages import TextPart, ThinkingPart, UserPromptPart

from tether.models.canonical import digest
from tether.models.testing import ScriptedModel
from tether.runtime.pydantic_ai.bridge import PortModel, run_agent
from tether.runtime.pydantic_ai.checkpoints import decode_conversation, encode_conversation
from tether.runtime.pydantic_ai.messages import (
    arguments,
    from_sdk_messages,
    from_sdk_response,
    to_sdk_messages,
    to_sdk_response,
)
from tether.types import (
    Message,
    ModelAttemptDenied,
    ModelExecutionLimits,
    ModelRequest,
    ModelResponse,
    ModelUnavailable,
    StreamItem,
    TokenUsage,
    ToolCall,
    ToolSpec,
)


async def authorize_history() -> None:
    pass


async def prepared(request: ModelRequest) -> ModelRequest:
    return request


@pytest.mark.parametrize("calls", [False, True])
def test_real_agent_text_and_deferred_proposals(
    model_request: ModelRequest, limits: ModelExecutionLimits, tool_spec: ToolSpec, calls: bool
) -> None:
    async def scenario() -> None:
        observed: list[ModelRequest] = []

        async def prepare(request: ModelRequest) -> ModelRequest:
            observed.append(request)
            return request

        response = ModelResponse(
            content="fabricated",
            finish_reason="tool_calls" if calls else "stop",
            tool_calls=(ToolCall(id="fabricated-call", name="lookup", arguments={"count": 2}),)
            if calls
            else (),
        )
        model = ScriptedModel([response])
        result = await run_agent(
            model,
            model_request.model_copy(update={"tools": (tool_spec,)}),
            limits,
            prepare=prepare,
            authorize_history=authorize_history,
        )
        assert result.response == response
        assert len(observed) == 1
        assert observed[0].system == model_request.system
        assert observed[0].messages == model_request.messages
        assert observed[0].tools == (tool_spec,)
        assert model.requests[0].tools[0].input_schema == tool_spec.input_schema
        assert result.response.usage is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "response",
    [
        ModelResponse(content="truncated", finish_reason="length"),
        ModelResponse(finish_reason="unknown"),
        ModelResponse(finish_reason="filtered"),
        ModelResponse(finish_reason="tool_calls"),
        ModelResponse(
            finish_reason="stop", tool_calls=(ToolCall(id="a", name="lookup", arguments={}),)
        ),
        ModelResponse(
            finish_reason="tool_calls", tool_calls=(ToolCall(id="a", name="unknown", arguments={}),)
        ),
        ModelResponse(
            finish_reason="tool_calls",
            tool_calls=(
                ToolCall(id="a", name="lookup", arguments={}),
                ToolCall(id="a", name="lookup", arguments={}),
            ),
        ),
    ],
)
def test_no_invalid_terminal_reaches_agent(
    model_request: ModelRequest,
    limits: ModelExecutionLimits,
    tool_spec: ToolSpec,
    response: ModelResponse,
) -> None:
    async def scenario() -> None:
        with pytest.raises(ModelUnavailable, match="invalid_"):
            await run_agent(
                ScriptedModel([response]),
                model_request.model_copy(update={"tools": (tool_spec,)}),
                limits,
                prepare=prepared,
                authorize_history=authorize_history,
            )

    asyncio.run(scenario())


def test_current_preparation_failure_stops_before_port(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    async def scenario() -> None:
        model = ScriptedModel([ModelResponse(finish_reason="stop")])

        async def deny(_: ModelRequest) -> ModelRequest:
            raise ModelAttemptDenied("read_session_denied")

        with pytest.raises(ModelAttemptDenied, match="read_session_denied"):
            await run_agent(
                model, model_request, limits, prepare=deny, authorize_history=authorize_history
            )
        assert not model.requests
        with pytest.raises(ModelAttemptDenied, match="invalid_model_call_binding"):
            await run_agent(
                model,
                model_request.model_copy(update={"attempts": None}),
                limits,
                prepare=prepared,
                authorize_history=authorize_history,
            )

    asyncio.run(scenario())


def test_deadline_cancellation_and_iterator_cleanup(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        closed = asyncio.Event()

        class BlockingPort:
            provider = "fabricated"

            async def stream(self, request: ModelRequest) -> AsyncIterator[StreamItem]:
                try:
                    entered.set()
                    await asyncio.Event().wait()
                    yield ModelResponse(finish_reason="stop")
                finally:
                    closed.set()

        with pytest.raises(ModelUnavailable, match="timeout"):
            await run_agent(
                BlockingPort(),
                model_request.model_copy(update={"timeout_seconds": 0.01}),
                limits,
                prepare=prepared,
                authorize_history=authorize_history,
            )
        assert closed.is_set()
        closed.clear()
        entered.clear()
        task = asyncio.create_task(
            run_agent(
                BlockingPort(),
                model_request,
                limits,
                prepare=prepared,
                authorize_history=authorize_history,
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()

    asyncio.run(scenario())


def test_repeated_logical_call_binding_is_refused(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    from pydantic_ai.models import ModelRequestParameters

    async def scenario() -> None:
        model = PortModel(
            ScriptedModel([ModelResponse(finish_reason="stop")]), model_request, limits, prepared
        )
        messages = to_sdk_messages(model_request.messages, model_request.system)
        await model.request(messages, None, ModelRequestParameters())
        with pytest.raises(ModelAttemptDenied, match="invalid_model_call_binding"):
            await model.request(messages, None, ModelRequestParameters())
        with pytest.raises(ModelAttemptDenied, match="unsupported_model_configuration"):
            await model.request(messages, {"temperature": 1}, ModelRequestParameters())

    asyncio.run(scenario())


def test_shared_response_usage_and_source_codec(
    tool_spec: ToolSpec, limits: ModelExecutionLimits
) -> None:
    response = ModelResponse(
        content="fabricated",
        finish_reason="tool_calls",
        tool_calls=(ToolCall(id="c", name="lookup", arguments={"count": 2}),),
        usage=TokenUsage(
            input_tokens=10, output_tokens=4, cache_read_tokens=None, reasoning_tokens=2
        ),
    )
    sdk = to_sdk_response(response, "fabricated")
    assert from_sdk_response(sdk) == response
    sdk.parts = [TextPart("changed")]
    with pytest.raises(ModelUnavailable, match="sdk_projection_changed"):
        from_sdk_response(sdk)
    for raw in ('{"a":1,"a":2}', "[1]", "{", "null", '{"x":NaN}'):
        with pytest.raises(ModelUnavailable, match="invalid_response"):
            arguments(raw)
    with pytest.raises(ModelUnavailable, match="unsupported_content"):
        from_sdk_response(SDKResponse(parts=[ThinkingPart("fabricated reasoning")]))


def test_messages_preserve_supported_tool_groups(tool_spec: ToolSpec) -> None:
    messages = (
        Message(role="user", content="fabricated"),
        Message(
            role="assistant",
            content="",
            tool_calls=(ToolCall(id="c", name=tool_spec.name, arguments={"count": 2}),),
        ),
        Message(role="tool", content="fabricated result", tool_call_id="c", is_error=True),
        Message(role="assistant", content="done"),
    )
    system, result = from_sdk_messages(to_sdk_messages(messages, "fabricated system"))
    assert system == "fabricated system"
    assert result == messages
    with pytest.raises(ModelUnavailable, match="invalid_history"):
        to_sdk_messages((Message(role="tool", content="forged", tool_call_id="missing"),))
    with pytest.raises(ModelUnavailable, match="unsupported_content"):
        from_sdk_messages([SDKRequest(parts=[UserPromptPart(["fabricated", "multimodal"])])])


def test_checkpoint_binding_and_version_refusal() -> None:
    conversation = Conversation(messages=[SDKRequest(parts=[UserPromptPart("fabricated")])])
    source = digest({"fabricated-source": 1})
    data = encode_conversation(conversation, source_sha256=source)
    expected = hashlib.sha256(data).hexdigest()
    restored = decode_conversation(data, expected_sha256=expected, source_sha256=source)
    assert from_sdk_messages(list(restored.messages))[1] == (
        Message(role="user", content="fabricated"),
    )
    with pytest.raises(ModelUnavailable, match="checkpoint_integrity_error"):
        decode_conversation(data, expected_sha256="0" * 64, source_sha256=source)
    with pytest.raises(ModelUnavailable, match="checkpoint_source_changed"):
        decode_conversation(data, expected_sha256=expected, source_sha256="0" * 64)
    value = json.loads(data)
    value["codec"] = "unsupported-v2"
    from tether.models.canonical import canonical

    data = canonical(value)
    with pytest.raises(ModelUnavailable, match="unsupported_checkpoint_codec"):
        decode_conversation(
            data, expected_sha256=hashlib.sha256(data).hexdigest(), source_sha256=source
        )
    with pytest.raises(ModelUnavailable, match="invalid_checkpoint"):
        decode_conversation(b"broken", expected_sha256=expected, source_sha256=source)


def test_history_gate_runs_before_conversion(
    model_request: ModelRequest, limits: ModelExecutionLimits, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tether.runtime.pydantic_ai import bridge

    async def denied() -> None:
        raise ModelAttemptDenied("read_session_denied")

    def unexpected(*args, **kwargs):
        raise AssertionError("history_disclosed_before_authorization")

    monkeypatch.setattr(bridge, "to_sdk_messages", unexpected)

    async def scenario() -> None:
        with pytest.raises(ModelAttemptDenied, match="read_session_denied"):
            await run_agent(
                ScriptedModel([]), model_request, limits, prepare=prepared, authorize_history=denied
            )

    asyncio.run(scenario())


def test_measured_usage_and_delta_cap(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    from tether.types import TextDelta

    async def scenario() -> None:
        usage = TokenUsage(input_tokens=5, output_tokens=2, reasoning_tokens=1)
        response = ModelResponse(content="fabricated", finish_reason="stop", usage=usage)
        result = await run_agent(
            ScriptedModel([response]),
            model_request,
            limits,
            prepare=prepared,
            authorize_history=authorize_history,
        )
        assert result.response.usage == usage
        oversized = ScriptedModel([TextDelta(text="é" * 5000), response])
        with pytest.raises(ModelUnavailable, match="response_too_large"):
            await run_agent(
                oversized,
                model_request,
                limits,
                prepare=prepared,
                authorize_history=authorize_history,
            )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("name", "outcome", "reason"),
    [
        ("forged", "success", "invalid_history"),
        ("lookup", "interrupted", "unsupported_content"),
        ("lookup", "denied", "unsupported_content"),
    ],
)
def test_sdk_tool_return_semantics_are_not_silently_changed(name, outcome, reason) -> None:
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart

    history = [
        SDKResponse(parts=[ToolCallPart("lookup", {}, "c")], finish_reason="tool_call"),
        SDKRequest(parts=[ToolReturnPart(name, "fabricated", "c", outcome=outcome)]),
    ]
    with pytest.raises(ModelUnavailable, match=reason):
        from_sdk_messages(history)


def test_empty_terminal_is_typed_and_does_not_trigger_a_second_call(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    async def scenario() -> None:
        model = ScriptedModel([ModelResponse(finish_reason="stop")])
        with pytest.raises(ModelUnavailable, match="empty_response"):
            await run_agent(
                model, model_request, limits, prepare=prepared, authorize_history=authorize_history
            )
        assert len(model.requests) == 1

    asyncio.run(scenario())


def test_sdk_output_tool_kind_is_not_reclassified_as_an_executable_function() -> None:
    from pydantic_ai.messages import ToolCallPart

    response = SDKResponse(
        parts=[ToolCallPart("lookup", {}, "c", tool_kind="output")], finish_reason="tool_call"
    )
    with pytest.raises(ModelUnavailable, match="unsupported_content"):
        from_sdk_response(response)
