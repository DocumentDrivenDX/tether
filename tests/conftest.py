"""Fabricated model fixtures shared by port and real-Agent conformance tests."""

from __future__ import annotations

import pytest
from functional_watchdog import MAX_FUNCTIONAL_WATCHDOG_MS

from tether.models.testing import ListAttemptRecorder
from tether.types import Message, ModelExecutionLimits, ModelRequest, RequestTags, ToolSpec
from tether.types.client import ClientLimits

pytest_plugins = ("test_client_reads",)


@pytest.fixture
def limits() -> ModelExecutionLimits:
    # The request window also covers the attempt recorder's journal commits, and each JSONL
    # commit syncs to disk. A loaded host can spend more than 5 seconds in those syncs and
    # turn a fabricated setup turn into a model timeout. Timeout tests set their own short
    # per-request windows; the canonical maximum leaves ordinary hang containment to the
    # outer functional test owner.
    return ModelExecutionLimits(
        max_response_bytes=8192,
        max_tool_argument_bytes=512,
        max_attempts=2,
        request_timeout_seconds=MAX_FUNCTIONAL_WATCHDOG_MS / 1000.0,
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


@pytest.fixture
def client_limits():
    return ClientLimits(
        max_page_size=100,
        max_call_ids=8,
        max_read_bytes=1024 * 1024,
        max_lookup_entries=1000,
        read_timeout_seconds=2,
        heartbeat_interval_seconds=0.02,
        shutdown_timeout_seconds=2,
        follower_queue_size=8,
        max_followers=8,
        max_active_turns=8,
        max_request_bytes=65536,
    )
