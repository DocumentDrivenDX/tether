"""Frozen fabricated cassette bytes expose schema and SDK serialization drift."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import pytest
from pydantic_ai import Conversation
from pydantic_ai.messages import ModelRequest as SDKRequest
from pydantic_ai.messages import UserPromptPart

from tether.history.jsonl import JsonlHistory
from tether.models import complete
from tether.models.canonical import digest
from tether.models.testing import RecordingModel, ReplayModel, ScriptedModel
from tether.runtime.pydantic_ai.checkpoints import decode_conversation, encode_conversation
from tether.stores.jsonl import JsonlRoot
from tether.types import Message, ModelRequest, ModelResponse, ModelUnavailable, TokenUsage
from tether.types.execution import Actor, HistoryRef
from tether.types.history import HistoryRecord

CASSETTE = Path(__file__).with_name("fixtures") / "cassette-v1.jsonl"
HISTORY = CASSETTE.with_name("history-v1.jsonl")
HISTORY_HEAD = CASSETTE.with_name("history-v1.head")
CHECKPOINT = CASSETTE.with_name("checkpoint-v1.json")
BOUND_VERSIONS = {
    "pydantic": "2.14.1",
    "pydantic-core": "2.50.1",
    "pydantic-ai-slim": "2.59.0",
    "pydantic-graph": "2.59.0",
}


def test_locked_sdk_versions_require_explicit_requalification() -> None:
    assert {name: version(name) for name in BOUND_VERSIONS} == BOUND_VERSIONS


def test_frozen_cassette_replays_and_records_identical_bytes(model_request: ModelRequest) -> None:
    frozen = CASSETTE.read_text(encoding="utf-8")
    response = ModelResponse(
        content="Fabricated answer.",
        finish_reason="stop",
        usage=TokenUsage(input_tokens=4, output_tokens=2, cache_read_tokens=0),
    )

    async def scenario() -> None:
        for mode in ("sequence", "keyed"):
            replay = ReplayModel(frozen, mode=mode)
            assert await complete(replay, model_request) == response
        lines: list[str] = []

        async def writer(line: str) -> None:
            lines.append(line)

        recorder = RecordingModel(ScriptedModel([response]), writer, fabricated=True)
        assert await complete(recorder, model_request) == response
        assert "".join(lines) == frozen

    asyncio.run(scenario())


def test_frozen_cassette_refuses_unsupported_version() -> None:
    frozen = CASSETTE.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="invalid_cassette"):
        ReplayModel(frozen.replace('"cassette":1', '"cassette":2', 1))


def test_frozen_sdk_checkpoint_round_trips_and_refuses_version_drift() -> None:
    source = digest({"fabricated-source": 1})
    frozen = CHECKPOINT.read_bytes()
    conversation = Conversation(
        conversation_id="fabricated-conversation",
        messages=[
            SDKRequest(
                parts=[
                    UserPromptPart(
                        "Fabricated checkpoint.",
                        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                    )
                ]
            )
        ],
    )
    assert encode_conversation(conversation, source_sha256=source) == frozen
    restored = decode_conversation(
        frozen,
        expected_sha256=hashlib.sha256(frozen).hexdigest(),
        source_sha256=source,
    )
    assert encode_conversation(restored, source_sha256=source) == frozen
    changed = frozen.replace(b'"sdk_version":"2.59.0"', b'"sdk_version":"0.0.0"', 1)
    with pytest.raises(ModelUnavailable, match="unsupported_checkpoint_codec"):
        decode_conversation(
            changed,
            expected_sha256=hashlib.sha256(changed).hexdigest(),
            source_sha256=source,
        )


def test_frozen_jsonl_history_reopens_and_new_writes_match(tmp_path: Path) -> None:
    actor = Actor(id="fabricated-owner")
    record = HistoryRecord(
        kind="message", message=Message(role="user", content="Fabricated history.")
    )
    expected = HistoryRef(
        provider="jsonl",
        key="fabricated-message",
        sha256="e5d1a2ea9b3bcd24f60e84a8406297626eb8caedd049c503412fa8b57d1d349a",
        codec="tether-history-v1",
        owner=actor.id,
        session_id="fabricated-session",
        kind="message",
        component="record",
    )
    source = HISTORY.read_bytes()
    head = HISTORY_HEAD.read_bytes().rstrip(b"\n")

    async def scenario() -> None:
        replay = tmp_path / "replay"
        replay.mkdir()
        (replay / "history.jsonl").write_bytes(source)
        (replay / "history.head").write_bytes(head)
        root = await JsonlRoot.open(replay)
        try:
            history = await JsonlHistory.open(root)
            value = (await history.read([expected], actor=actor))[0]
            assert value.status == "ready"
            assert value.record == record
            assert (
                await history.put(
                    actor=actor,
                    session_id="fabricated-session",
                    key="fabricated-message",
                    record=record,
                )
                == expected
            )
        finally:
            assert await root.aclose()
        assert (replay / "history.jsonl").read_bytes() == source
        assert (replay / "history.head").read_bytes() == head

        fresh = tmp_path / "fresh"
        root = await JsonlRoot.open(fresh)
        try:
            history = await JsonlHistory.open(root)
            assert (
                await history.put(
                    actor=actor,
                    session_id="fabricated-session",
                    key="fabricated-message",
                    record=record,
                )
                == expected
            )
        finally:
            assert await root.aclose()
        assert (fresh / "history.jsonl").read_bytes() == source
        assert (fresh / "history.head").read_bytes() == head

    asyncio.run(scenario())
