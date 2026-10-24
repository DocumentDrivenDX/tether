"""Fabricated model fixtures shared by port and real-Agent conformance tests."""

from __future__ import annotations

import pytest

from tether.models.testing import ListAttemptRecorder
from tether.types import Message, ModelExecutionLimits, ModelRequest, RequestTags, ToolSpec


@pytest.fixture
def limits() -> ModelExecutionLimits:
    return ModelExecutionLimits(
        max_response_bytes=8192,
        max_tool_argument_bytes=512,
        max_attempts=2,
        request_timeout_seconds=5,
        max_retry_delay_seconds=0.001,
    )


@pytest.fixture
def model_request() -> ModelRequest:
    tags = RequestTags(
        session_id="fabricated-session",
        turn_id="fabricated-turn",
        model_call_id="fabricated-call",
        principal="fabricated-actor",
        spec_id="demo",
        spec_version="1",
    )
    return ModelRequest(
        model="demo",
        tags=tags,
        system="Fabricated instructions.",
        messages=(Message(role="user", content="Fabricated request."),),
        identity_mode="anonymous",
        max_output_tokens=64,
        attempts=ListAttemptRecorder(tags),
    )


@pytest.fixture
def tool_spec() -> ToolSpec:
    return ToolSpec(
        name="lookup",
        description="Fabricated lookup.",
        input_schema={
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"],
            "additionalProperties": False,
        },
    )
